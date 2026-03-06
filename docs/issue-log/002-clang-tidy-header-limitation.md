# Issue #002: clang-tidy 헤더 미존재 시 정밀 분석 불가

|             |                                      |                                                                                           |
| ----------- | ------------------------------------ | ----------------------------------------------------------------------------------------- |
| **이슈 구분**   | **문제 상황 및 원인**                       | **리서치 및 해결 과정 (Reference & Solution)**                                                    |
| **Problem** | C 소스 파일에 `#include <pfmcom.h>` 등 헤더 import가 존재하는데, clang-tidy 실행 환경에 해당 헤더 파일이 없으면 **첫 번째 `#include`에서 fatal error가 발생하여 파싱을 즉시 중단**함. 이후 코드(typedef, 함수 본문 등)를 전혀 읽지 못해 `svc_cnt` 미초기화 등 핵심 버그를 탐지할 수 없음 | - **원인:** clang-tidy는 Clang 컴파일러 프론트엔드 기반으로, `#include`를 실제 컴파일처럼 처리함. 헤더 파일이 디스크에 없으면 `error: file not found`를 발생시키고 AST(구문 트리) 생성을 포기함. 파일 내에 `typedef struct ... ctx_t;` (143번줄)가 있어도, 그 전인 43번줄 `#include <pfmcom.h>`에서 이미 중단되어 143번줄까지 도달하지 못함 |
| **증상** | 2932줄 C 파일에서 44개 warning이 나왔지만, 전부 **텍스트 수준 패턴**(bugprone-branch-clone 37개, bugprone-narrowing-conversions 7개)만 탐지. 메모리 안전성 관련 warning(`clang-analyzer-*`)은 0개 | - **확인:** `clang-tidy -- 2>&1 \| grep "2391"` → 결과 없음. svc_cnt가 있는 line 2391에 대한 warning이 전무. 44개 warning은 Clang의 error recovery 모드에서 부분적으로 텍스트 패턴만 체크한 결과 |
| **근본 원인** | 폐쇄망 환경에서는 프로젝트 헤더 파일을 분석 환경에 함께 제공하기 어려움. Mider는 `-f file.c` 단위로 개별 파일을 받아 분석하므로, 프로젝트 빌드 환경(Makefile, compile_commands.json, 헤더 경로)이 없는 상태에서 실행됨 | - **배경:** `#include`가 50개 이상인 C 파일에서 **첫 번째 헤더**부터 없으면 clang-tidy는 사실상 무용지물. 이 파일의 경우 43~97번줄에 50개 include가 있으며, 43번줄 `pfmcom.h`에서 즉시 중단 |

---

## clang-tidy의 분석 레벨과 헤더 의존성

### clang-tidy가 하는 일

clang-tidy는 크게 2가지 레벨로 코드를 분석한다:

#### Level 1: 텍스트/구문 패턴 (헤더 없이도 동작)

AST의 부분적 정보만으로도 탐지 가능한 패턴. 헤더가 없어도 C 문법 구조 자체로 판단.

| 체크 | 탐지 내용 | 예시 |
|------|----------|------|
| `bugprone-branch-clone` | if/else 분기 코드 동일 | `if(x) { foo(); } else { foo(); }` |
| `bugprone-narrowing-conversions` | int→char 축소 변환 | `char c = func_returning_int();` |
| `bugprone-sizeof-expression` | sizeof 오용 | `sizeof(ptr)` vs `sizeof(*ptr)` |
| `bugprone-string-literal-with-embedded-nul` | 문자열 내 NULL | `"hello\0world"` |

**이번 파일에서 나온 44개 warning이 전부 이 레벨.**

#### Level 2: 데이터 흐름 분석 (헤더 필수)

변수의 선언 → 초기화 → 사용 → 해제 전체 흐름을 추적. **AST 완성이 필수.**

| 체크 | 탐지 내용 | svc_cnt 탐지 가능? |
|------|----------|------------------|
| `clang-analyzer-core.uninitialized.Assign` | 미초기화 변수 사용 | **O** (핵심!) |
| `clang-analyzer-core.NullDereference` | NULL 포인터 역참조 | O |
| `clang-analyzer-unix.Malloc` | malloc 후 free 누락 | O |
| `clang-analyzer-deadcode.DeadStores` | 사용되지 않는 변수 할당 | O |
| `clang-analyzer-security.insecureAPI.strcpy` | strcpy 보안 이슈 | O |

**이 레벨은 헤더 없이 0개 동작.**

### 왜 헤더가 없으면 Level 2가 안 되는가

clang-tidy는 `#include`를 실제 컴파일과 동일하게 처리한다. 헤더 파일이 디스크에 없으면 **fatal error**로 파싱을 즉시 중단한다.

```c
// ordsb0100010t01.c

#include <pfmcom.h>   // ← 43번줄: error: 'pfmcom.h' file not found
#include <pfmutil.h>  // ← 44번줄: 여기부터 읽히지 않음
...                   // ← 50개 include 전부 무시
                      //
                      // ↓↓↓ 아래 코드도 전부 파싱되지 않음 ↓↓↓

typedef struct ordsb0100010t01_ctx_s ordsb0100010t01_ctx_t;  // 143번줄: 도달 못함

void c400_get_rcv_chgreq_possible(ordsb0100010t01_ctx_t *ctx) {  // 2382번줄
    long svc_cnt;               // 2391번줄: 파싱 포기 상태라 추적 불가
    currsvclist_s[svc_cnt] = ...  // svc_cnt 미초기화 사용 → 탐지 불가
}
```

핵심: **typedef가 파일 안에 있어도 소용없다.** 43번줄 `#include <pfmcom.h>`에서 이미 중단되었으므로 143번줄의 typedef까지 도달하지 못한다. 44개 warning은 Clang의 error recovery 모드에서 AST 없이 텍스트 패턴만 부분 체크한 결과이다.

---

## 현재 상태 비교

| 방식 | svc_cnt 탐지 | 장점 | 한계 |
|------|-------------|------|------|
| **clang-tidy** (헤더 없음) | X | 분기 중복, 타입 축소 탐지 | 메모리 안전성 분석 전혀 불가 |
| **Heuristic Scanner** (regex) | O (regex 매칭) | 헤더 없이도 6종 패턴 탐지 | false positive 많음, Pass 2에서 LLM 누락 발생 |
| **clang-tidy** (헤더 있음) | O | 데이터 흐름 추적, 정밀 분석 | 폐쇄망에서 헤더 확보 어려움 |

---

## 해결 방안

### 방안 1: clang-tidy + Heuristic 병행 (권장)

clang-tidy가 있어도 Heuristic Scanner를 **항상 함께 실행**하여 두 결과를 합친다.

```
clang-tidy warnings (44개: branch-clone, narrowing)
  + Heuristic findings (494개: UNINIT_VAR, UNSAFE_FUNC, ...)
  → 중복 제거 후 합산
  → LLM에 전달
```

**장점**: 헤더 유무에 관계없이 최대 커버리지 확보
**구현**: `c_analyzer.py`의 `_run_clang_tidy()` 결과와 `CHeuristicScanner` 결과를 합치는 로직 추가

### 방안 2: compile_commands.json 지원 (장기)

프로젝트 빌드 환경을 `-c compile_commands.json` 옵션으로 전달받아 clang-tidy에 넘긴다.

```bash
mider -f file.c -c build/compile_commands.json
```

**장점**: clang-tidy Level 2 정밀 분석 가능
**한계**: 폐쇄망에서 빌드 환경 확보가 전제 조건

### 방안 3: 헤더 스텁 자동 생성 (실험적)

누락된 헤더의 구조체/타입을 소스 코드에서 추론하여 임시 스텁 헤더를 생성한다.

```c
// auto-generated stub for pfmcom.h
typedef struct { /* unknown */ } ordsb0100010t01_ctx_t;
typedef struct { /* unknown */ } currsvclist_t;
```

**장점**: clang-tidy가 부분적으로라도 AST 생성 가능
**한계**: 구조체 필드 정보를 모르면 정밀도 낮음, 구현 복잡

---

## 우선 적용 계획

**방안 1 (clang-tidy + Heuristic 병행)** 을 우선 적용한다.

현재 `c_analyzer.py`의 분기:
```
clang-tidy 있음 → Error-Focused (clang-tidy warnings만 사용)
clang-tidy 없음 + >500줄 → 2-Pass Heuristic
clang-tidy 없음 + ≤500줄 → 단일 Heuristic
```

변경 후:
```
clang-tidy 있음 → Error-Focused (clang-tidy warnings + Heuristic findings 합산)
clang-tidy 없음 + >500줄 → 2-Pass Heuristic (현재와 동일)
clang-tidy 없음 + ≤500줄 → 단일 Heuristic (현재와 동일)
```

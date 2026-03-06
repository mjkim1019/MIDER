# Issue #002: clang-tidy 헤더 미존재 시 정밀 분석 불가

|             |                                      |                                                                                           |
| ----------- | ------------------------------------ | ----------------------------------------------------------------------------------------- |
| **이슈 구분**   | **문제 상황 및 원인**                       | **리서치 및 해결 과정 (Reference & Solution)**                                                    |
| **Problem** | clang-tidy가 설치되어 있어도 프로젝트 헤더(`pfmcom.h` 등)가 없으면 **AST(구문 트리)를 완성하지 못함**. `clang-analyzer-*` 체크(데이터 흐름 분석)가 전혀 동작하지 않아 `svc_cnt` 미초기화 등 핵심 버그를 탐지할 수 없음 | - **원인:** clang-tidy는 LLVM/Clang 컴파일러 프론트엔드 기반. 헤더 없이는 타입 정보, 구조체 정의, 함수 시그니처를 알 수 없어 AST 생성 실패. AST 없으면 `clang-analyzer-core.uninitialized.Assign` 같은 정밀 체크 불가 |
| **증상** | 2932줄 C 파일에서 45개 warning이 나왔지만, 전부 **텍스트 수준 패턴**(bugprone-branch-clone 37개, bugprone-narrowing-conversions 7개)만 탐지. 메모리 안전성 관련 warning은 0개 | - **확인:** `clang-tidy -- 2>&1 | grep "2391"` → 결과 없음. svc_cnt가 있는 line 2391에 대한 warning이 전무 |
| **근본 원인** | 폐쇄망 환경에서는 프로젝트 전체 소스/헤더를 분석 대상으로 넘기기 어려움. 개별 파일 단위 분석이 기본 전제인데, 개별 파일만으로는 clang-tidy의 정밀 분석 불가 | - **배경:** Mider는 `-f file.c` 단위로 파일을 받아 분석. 프로젝트 빌드 환경(Makefile, compile_commands.json)이 없는 상태 |

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

```c
#include <pfmcom.h>   // ← 43번줄에서 error: file not found
                      //   여기서 파싱 중단

// pfmcom.h 안에 정의된 것들:
//   - ordsb0100010t01_ctx_t 구조체
//   - currsvclist_t 타입
//   - RC_NRM, RC_ERR 상수
//   - 각종 매크로, 함수 선언

// 아래 코드에서 clang-tidy가 모르는 것들:
void c400_get_rcv_chgreq_possible(ordsb0100010t01_ctx_t *ctx) {
//                                ^^^^^^^^^^^^^^^^^^^^^^^^ 타입 모름
    long svc_cnt;               // 선언은 보임
    currsvclist_s[svc_cnt] = ...  // currsvclist_s 타입 모름
//                                  → svc_cnt가 인덱스로 쓰이는지 추적 불가
//                                  → "미초기화 변수 사용" 판정 불가
}
```

clang-tidy는 `ordsb0100010t01_ctx_t`가 뭔지, `currsvclist_s`가 배열인지 포인터인지 모르기 때문에 데이터 흐름 분석 자체를 포기한다.

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

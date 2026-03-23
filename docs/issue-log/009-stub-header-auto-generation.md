# 이슈 #009: clang-tidy stub 헤더 자동 생성

## 발견일
2026-03-23

## 발견 경위
이슈 #002, #006에서 clang-tidy 헤더 누락으로 Level 2(데이터 흐름 분석)가 전혀 동작하지 않는 문제를 확인. T27에서 헤더 에러 필터링 + Heuristic fallback으로 우회했으나, clang-tidy 자체의 정밀 분석을 활용하지 못하는 한계가 남아있었음.

## 문제 상황

### 배경
- 이슈 #002: 헤더 없으면 clang-tidy가 fatal error로 파싱 중단 → `clang-analyzer-*` 0건
- 이슈 #006: 헤더 에러가 유의미 경고로 오분류 → Error-Focused 경로 오진입
- T27: 헤더 에러 필터링으로 Heuristic fallback 확보 → 그러나 clang-tidy Level 2는 여전히 사용 불가

### 기존 접근의 한계
T27 이후의 분기:
```
clang-tidy 실행 → 헤더 에러 필터링 → Level 2 = 0건 → None 반환 → Heuristic/2-Pass fallback
```
- clang-tidy를 실행하긴 하나, 헤더가 없으면 **항상 Heuristic으로 빠짐**
- `clang-analyzer-core.uninitialized.Assign` 등 데이터 흐름 분석을 전혀 활용 못함
- Heuristic(regex)은 false positive가 많고, 대형 함수 압도 문제(이슈 #003)도 있음

## 해결: StubHeaderGenerator (feat/c-analyzer-stub-tuning)

### 접근 방식
이슈 #002 방안 3("헤더 스텁 자동 생성")을 구현. 소스 파일의 `#include`를 파싱하여 임시 stub 헤더를 생성하고, clang-tidy에 `-I` 플래그로 전달.

### 구현 내용

#### 새 모듈: `mider/tools/static_analysis/stub_header_generator.py`
- `#include <*.h>`, `#include "*.h"` 모두 파싱 (정규식: `^\s*#\s*include\s*[<"]([^>"]+\.h)[>"]`)
- stub 헤더에 임베디드 C 공통 타입 정의 포함:
  - `UINT8/16/32/64`, `INT8/16/32/64`, `BOOL`
  - `TRUE`, `FALSE`, `NULL` 매크로
- `settings.yaml`의 `stub_extra_types`로 프로젝트별 커스텀 타입 추가 가능
- 서브 경로 헤더(e.g. `util/types.h`)도 자동으로 디렉토리 생성
- 인코딩 fallback: UTF-8 → CP949

#### ClangTidyRunner 수정: `mider/tools/static_analysis/clang_tidy_runner.py`
```python
# 변경 전
cmd = [str(self._binary), f"--checks={checks_arg}", str(file_path), "--"]

# 변경 후
stub_gen = StubHeaderGenerator()
stubs_dir = file_path.parent / "stubs"
stub_gen.generate(str(file_path), stubs_dir)
cmd = [..., "--", f"-I{stubs_dir}", "-std=c99"]
# finally 블록에서 stub_gen.cleanup(stubs_dir) 호출
```

#### 실행 흐름
```
1. 소스 파일에서 #include 파싱 → 헤더 목록 추출
2. stubs/ 디렉토리에 stub 헤더 생성 (공통 타입 + 커스텀 타입)
3. clang-tidy 실행 (-I stubs/ 로 include path 추가)
4. finally 블록에서 stubs/ 디렉토리 삭제 (분석 환경 오염 방지)
```

### 추가 변경사항
- 기본 체크에 `-bugprone-branch-clone` 추가: 헤더 불완전 시 오탐이 많은 Level 1 체크 비활성화
- `c_analyzer_error_focused.txt`, `c_analyzer_heuristic.txt`, `c_prescan_fewshot.txt` 프롬프트 업데이트
- `settings_loader.py`에 `get_stub_extra_types()` 함수 추가

## 기대 효과

| 항목 | T27 이후 (필터링만) | stub 생성 후 |
|------|---------------------|-------------|
| clang-tidy 파싱 | fatal error → 중단 | stub으로 파싱 계속 |
| Level 2 (clang-analyzer-*) | 0건 (항상 fallback) | 부분적 동작 가능 |
| 미초기화 변수 탐지 | Heuristic regex만 | clang-analyzer + regex 병행 |
| stub 정밀도 | — | 구조체 필드 정보 없음 → 한계 있음 |

## 한계 및 향후 과제

1. **구조체 필드 미정의**: stub에 `typedef int BOOL;` 같은 기본 타입만 있고, 프로젝트 고유 구조체 필드는 알 수 없음 → 구조체 멤버 접근 관련 Level 2 경고는 여전히 미탐지
2. **함수 시그니처 미정의**: 헤더에 선언된 함수 프로토타입이 없으면 `implicit function declaration` 경고 발생 가능
3. **프로젝트별 커스텀 타입**: `settings.yaml`의 `stub_extra_types`를 프로젝트마다 설정해야 함 → 자동 추론 미지원
4. **실제 효과 검증 필요**: stub 생성 후 `clang-analyzer-*` 경고가 실제로 몇 건이나 늘어나는지 실측 필요

## 관련
- 이슈 #002: clang-tidy 헤더 미존재 시 정밀 분석 불가 (상위 문서, 방안 3 구현)
- 이슈 #006: clang-tidy 헤더 에러 시 Error-Focused 오진입 (선행 해결)
- 이슈 #003: Pass 2 대형 함수 압도 문제
- PR #30: feat/c-analyzer-stub-tuning

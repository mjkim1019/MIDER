# 맥락 노트

## 설계 결정
- Bottom-up 구현 순서: 기반 → 스키마 → 인프라 → Tool → Agent → CLI → 통합
- T4/T6/T7/T8 병렬 구현: 모두 T3(Base Infrastructure)만 의존하므로 독립적
- T11/T12 병렬 구현: Phase 2 Analyzer와 Phase 3 Reporter는 스키마가 확정되어 있으므로 병렬 가능
- LSP Tool (T7)은 1차 PoC에서 선택적 기능 — 바이너리 없을 시 graceful degradation
- ContextCollectorAgent는 Tool 기반 추출 + LLM 보정 하이브리드 방식 채택 (TaskClassifierAgent 패턴 동일)

## 토큰 최적화 설계 결정 (Structure + Function Window)
- **Error-Focused 경로**: `{file_content}` (파일 전체) → `{structure_summary}` + `{error_functions}` (에러 포함 함수 전체)
- **에러 포함 함수 전체 추출**: ±N줄이 아닌 함수 단위 — 함수는 논리적 완결 단위이므로 LLM이 더 정확하게 분석 가능
- **구조 요약**: Phase 1 file_context의 imports/calls/patterns + ast-grep 함수 시그니처 + 전역변수
- **Heuristic 경로**: 에러 위치를 모르므로 파일 크기 기반 분기 — ≤500줄 전체, >500줄 head(200)+tail(100)+구조요약
- **SQL 특화**: SQL은 함수가 아닌 SQL 문(SELECT/INSERT/UPDATE/DELETE) 단위로 추출
- **구현 위치**: 4개 Analyzer의 `_build_messages()` + 8개 프롬프트 템플릿 변수 변경

## T18 설계 결정 (SQL 성능개선 강화)
- **SQL 문법 검증**: sqlparse 라이브러리 활용 — Oracle SQL 파싱 후 syntax error 추출
- **ExplainPlan 별도 Pydantic 스키마 생략**: 다른 Tool(ESLint, clang-tidy, proc)과 동일하게 ToolResult.data dict로 전달 — Agent에서 직접 dict 접근
- **Explain Plan 입력**: CLI `--explain-plan` 옵션으로 파일 경로 전달
- **통계정보**: Explain Plan 내의 Cost/Rows/Bytes가 통계정보 — 별도 통계 파일 불필요
- **튜닝 포인트**: Full Table Scan, Cartesian Join, 높은 Cost 등 비효율 오퍼레이션 자동 탐지
- **LLM 역할**: 문법 오류 설명 + Explain Plan 해석 + 튜닝 제안 (한국어)

## T20 설계 결정 (C Heuristic Pre-Scanner)
- **2-Pass 전략**: Pass 1(regex + gpt-4o-mini 선별) → Pass 2(gpt-4o 심층분석). 전체 파일을 gpt-4o로 보내는 것보다 비용 효율적
- **regex 사전 스캔**: 전체 파일에서 위험 패턴 6종을 즉시 탐지 (비용 0). clang-tidy 대체
- **few-shot 프롬프트**: 사용자가 위험 패턴 예시를 추가/수정 가능한 구조. `c_prescan_fewshot.txt`에서 관리
- **500줄 분기 유지**: 500줄 이하 파일은 전체 코드를 LLM에 보낼 수 있으므로 기존 Heuristic 유지
- **함수 매핑**: 기존 `_find_function_boundaries()` 재사용 → 위험 패턴이 어떤 함수에 있는지 매핑
- **Error-Focused 경로 재활용**: Pass 2는 기존 `c_analyzer_error_focused` 프롬프트 사용 → 새 프롬프트 불필요

## T21 설계 결정 (Pass 2 함수별 개별 LLM 호출)
- **문제**: 4개 함수(2042줄)를 한 번에 LLM에 전달하면 대형 함수(c100+c200)만 분석하고 소형 함수(c400, c700) 누락
- **해결**: 함수별 개별 LLM 호출 — 각 함수를 독립적으로 분석하여 attention 분산 방지
- **비용**: 입력 토큰 총량 동일 (2042줄 → 636+1115+127+164 = 동일), output은 함수별 별도
- **병렬화**: asyncio.gather()로 동시 호출, semaphore로 rate limit 보호
- **MIDER_EXCLUDE_FUNCTIONS 제거**: 임시 workaround 삭제, 근본 해결로 대체

## T22 설계 결정 (clang-tidy + Heuristic 하이브리드)
- **문제**: clang-tidy는 헤더 없으면 Level 2(데이터 흐름) 분석 불가, Heuristic은 regex로 UNINIT_VAR 등 탐지 가능
- **해결**: clang-tidy 있어도 Heuristic Scanner를 항상 함께 실행, 결과 합산
- **중복 제거**: 같은 라인(±2) + 같은 카테고리 → clang-tidy 우선
- **변경 범위**: `c_analyzer.py`의 Error-Focused 경로만 수정 — Heuristic/2-Pass 경로는 영향 없음

## T19 설계 결정 (Proframe XML 지원)
- **XML 유형**: Proframe WebSquare(Inswave) 화면 정의 XML — w2:dataList, w2:column, ev:on* 이벤트
- **JS 교차 검증**: XML의 ev:on* 이벤트 핸들러가 대응하는 JS 파일에 존재하는지 확인
- **JS 파일 매칭**: XML 파일명과 동일 패턴의 JS 파일 탐색 (같은 디렉토리 or 패턴 매칭)
- **분석 범위**: ID 중복 검사, 이벤트 핸들러 존재 검증, 데이터 바인딩 구조 검증
- **Agent**: XMLAnalyzerAgent 신규 추가, gpt-4o-mini (fallback gpt-4o)
- **배포 체크리스트**: XML → 화면 배포 섹션(섹션 1)에 매핑

## T10 설계 결정
- **Tool 우선 추출**: AstGrepSearch로 import/함수 호출/패턴을 먼저 추출, LLM은 보정만 담당
- **LLM graceful degradation**: LLM 실패 시 Tool 결과만으로 FileContext 생성 (TaskClassifierAgent 패턴)
- **프롬프트**: `context_collector.txt` 이미 구현됨 — execution_plan, file_contents 변수 사용
- **FileContext 모델**: `models/file_context.py` 이미 구현됨 — SingleFileContext, ImportInfo, CallInfo, PatternInfo
- **DependencyGraph 재사용**: ExecutionPlan의 dependencies를 그대로 FileContext에 전달

## 참조 문서
- docs/TECH_SPEC.md: Agent 워크플로우 전체 (섹션 2, Agent 3: ContextCollectorAgent)
- docs/DATA_SCHEMA.md: Pydantic 스키마 정의 (섹션 2: FileContext)
- docs/CLI_SPEC.md: CLI 옵션, 터미널 출력 형식
- docs/manuals/agents.md: BaseAgent 패턴, call_llm() 재시도

## 주의사항
- 1차 PoC 범위: RAG, Session Resume 제외 (토큰 최적화는 1차에 포함)
- print() 금지 → rich/logging 사용
- Agent는 코드 수정 불가 (제안만)
- Before/After 코드는 1-3줄만
- `**kwargs` 남용 금지 — 명시적 파라미터 사용 (TaskClassifierAgent에서 이미 적용됨)
- tests/fixtures/sample_skb/는 참조용 — 절대 커밋 금지

## 변경 이력
| 날짜 | 내용 | 이유 |
|------|------|------|
| 2026-02-24 | 최초 계획 수립 | 전체 개발 계획 |
| 2026-02-26 | BaseAgent call_llm() 재시도 시 exponential backoff 추가 | 리뷰에서 rate limit 대응 필요 지적 |
| 2026-02-26 | BaseAgent fallback 시 self.model 변경하지 않음 (스펙과 의도적 차이) | Agent 상태 오염 방지 |
| 2026-02-26 | LLMClient empty choices 가드 추가 | 리뷰에서 content filter 시 빈 응답 가능성 지적 |
| 2026-02-27 | `_PACKAGE_DIR` 경로 `parent.parent` → `parent.parent.parent` 수정 (3개 runner) | `static_analysis/` 하위 파일에서 `.parent.parent`는 `mider/tools/`를 가리켜 바이너리/설정을 찾지 못함 |
| 2026-02-27 | ESLint severity 분기: `else` → `elif severity == 1` | severity 0(off)인 rule 결과가 warning으로 잘못 분류되는 버그 |
| 2026-02-27 | ProcRunner `last_pcc_code` 상태 변수 도입 | proc 출력에서 PCC 에러 코드와 Semantic error가 다른 라인에 있어 연결 실패 |
| 2026-02-27 | ProcRunner `oname=/dev/null` → `os.devnull` | macOS/Windows 호환성 확보 |
| 2026-02-27 | 3개 runner에서 `**kwargs` 제거 | CLAUDE.md 컨벤션: `*args, **kwargs` 남용 금지, 명시적 파라미터 선호 |
| 2026-02-27 | ESLint `ruleId` null 처리: `get("ruleId", "unknown")` → `get("ruleId") or "unknown"` | ESLint parser error 시 `ruleId: null`이 오면 Python `None`이 반환되어 `"unknown"` fallback 실패 |
| 2026-02-27 | LSPClient `column` 기본값 0 → 1 변경 | `column - 1`로 0-based 변환 시 default 0이면 -1이 되어 잘못된 LSP 위치 전송 |
| 2026-02-27 | LSP URI 파싱: `str.replace("file://", "")` → `urllib.parse.urlparse` + `unquote` | 공백/특수문자 포함 경로에서 percent-encoding 처리 실패 |
| 2026-02-27 | LSP 전체 핸드셰이크 시퀀스 구현 (initialize→initialized→didOpen→request→shutdown) | initialize 없이 바로 요청하면 대부분의 LSP 서버가 응답 거부 |
| 2026-02-27 | `_extract_response()` Content-Length 기반 멀티메시지 파싱 + request_id 매칭 | LSP 서버가 여러 JSON-RPC 메시지를 stdout에 출력하므로 단순 split으로는 실제 요청 응답을 찾을 수 없음 |
| 2026-02-27 | subprocess 호출 시 `cwd` 파라미터 추가, returncode/stderr 로깅 | LSP 서버가 프로젝트 루트 기준으로 동작해야 정확한 분석 가능, 에러 진단을 위한 로깅 필요 |
| 2026-02-27 | `RiskAssessment.deployment_allowed` description 수정: `critical == 0` → `critical == 0 and high < 3` | reporter 프롬프트의 배포 차단 로직(high 3개 이상도 차단)과 Pydantic 모델 description이 불일치 |
| 2026-02-27 | `sql_analyzer_error_focused.txt`에 `file_context` 변수 추가 | 다른 3개 error_focused 프롬프트(JS/C/ProC)에는 file_context가 있으나 SQL만 누락, Phase 2 일관성 확보 |
| 2026-02-27 | TECH_SPEC.md의 `estimated_effort` 필드는 1차 PoC에서 의도적으로 제외 | report.py와 reporter.txt 모두 해당 필드 없이 구현, 2차에서 추가 예정 |
| 2026-02-27 | TaskClassifierAgent `run()` 시그니처에서 `**kwargs` 제거 | CLAUDE.md 컨벤션: `**kwargs` 남용 금지, 명시적 파라미터 선호 |
| 2026-02-27 | 빈 파일 목록 시 `DependencyGraph()` 모델 인스턴스 사용 | raw dict 대신 Pydantic 모델을 사용하여 타입 안전성 확보 |
| 2026-02-27 | `_apply_llm_priorities`에서 priority 0 처리: `if priority` → `isinstance(priority, int)` | Python에서 0이 falsy이므로 priority 0이 무시되는 버그 방지 |
| 2026-02-27 | LLM 응답 `json.loads` 후 `isinstance(dict)` 타입 체크 추가 | LLM이 list 등 비-dict JSON을 반환할 경우 AttributeError 방지 |
| 2026-03-04 | T10~T15 계획 수립, T10부터 재개 | T1~T9 완료 후 후속 개발 |
| 2026-03-04 | FileReader import를 `__init__` 내부 → 모듈 레벨로 이동 | TaskClassifierAgent와 패턴 일치, 리뷰 반영 |
| 2026-03-04 | 주석 필터 `startswith("*")` → `startswith("* ")` | `*ptr = malloc(...)` 같은 포인터 역참조가 주석으로 오인되는 버그 방지 |
| 2026-03-04 | `common_patterns`를 Tool 결과 우선으로 변경 | LLM이 빈도 수치를 할루시네이션할 수 있으므로 정확한 Tool 집계 사용 |
| 2026-03-04 | JS/C/ProC Analyzer: gpt-4o(fallback 없음), SQL Analyzer: gpt-4o-mini(fallback gpt-4o) | TECH_SPEC 스펙 준수 — 복잡한 분석은 gpt-4o, SQL 패턴은 gpt-4o-mini |
| 2026-03-04 | ProC Error-Focused 조건: proc 에러 OR SQLCA 미검사 블록 존재 | TECH_SPEC "errors 있거나 sqlca_check 누락" 조건 |
| 2026-03-04 | `file_context_str` 연산을 Error-Focused 분기 안으로 이동 (JS/C) | Heuristic 프롬프트에 file_context 변수가 없으므로 불필요 연산 제거 (리뷰 반영) |
| 2026-03-04 | SQL analyzer `match["line"]` → `match.get("line", 0)` 안전 접근 | AstGrepSearch 결과에 키 누락 시 KeyError 방지 (리뷰 반영) |
| 2026-03-04 | `llm_tokens_used` 추정값 사용: `(len(prompt) + len(response)) // 4` | LLMClient.chat()이 토큰 수를 반환하지 않으므로 근사값 사용 (2차에서 개선 예정) |
| 2026-03-04 | JS/C Analyzer: `file_context_str` 연산을 Error-Focused 분기 안으로 이동 | Heuristic 프롬프트에 file_context 변수가 없으므로 불필요 연산 제거 (리뷰 반영) |
| 2026-03-04 | SQL analyzer `match["line"]` → `match.get("line", 0)` 안전 접근 | AstGrepSearch 결과에 키 누락 시 KeyError 방지 (리뷰 반영) |
| 2026-03-04 | `llm_tokens_used` 추정값 사용: `(len(prompt) + len(response)) // 4` | LLMClient.chat()이 토큰 수를 반환하지 않으므로 근사값 사용 (2차에서 개선 예정) |
| 2026-03-04 | 토큰 최적화 설계: `{file_content}` → `{structure_summary}` + `{error_functions}` | 대형 파일에서 LLM 토큰 과다 소비 방지, 함수 단위 추출로 논리적 완결성 확보 |
| 2026-03-04 | Context 압축을 2차 PoC에서 1차 PoC로 이동 | 토큰 최적화가 1차 PoC 비용 효율성에 직결되므로 조기 적용 |
| 2026-03-04 | ReporterAgent: LLM은 risk_description 생성에만 사용, 집계는 코드에서 처리 | 통계 집계는 정확해야 하므로 LLM 할루시네이션 방지, LLM은 한국어 설명 생성에 집중 |
| 2026-03-04 | ReporterAgent: gpt-4o-mini (temp 0.3, fallback gpt-4o) | TECH_SPEC 스펙 준수 — 간단한 요약이므로 경량 모델 |
| 2026-03-04 | `_determine_risk()`에서 `issue["issue_id"]` → `issue.get("issue_id", "")` | 리뷰 반영: issue dict에 issue_id 키 누락 시 KeyError 방지 |
| 2026-03-04 | `_generate_risk_description()`에 generated_at 매개변수 전달 | 리뷰 반영: 리포트 전체의 timestamp 일관성 확보 |
| 2026-03-04 | OrchestratorAgent: Sub-Agent lazy init (None이면 초기화) | 테스트에서 mock 주입 후 run() 호출 시 덮어쓰기 방지 |
| 2026-03-04 | OrchestratorAgent: LLM 직접 호출 없음, Sub-Agent에 위임 | Orchestrator는 워크플로우 제어만 담당, 프롬프트는 사용하지 않음 |
| 2026-03-04 | OrchestratorAgent: ProgressCallback Protocol 정의 | Rich Progress Bar 연동을 위한 타입 안전 콜백 인터페이스 |
| 2026-03-04 | Phase 2 루프에 sub-task KeyError 방어 추가 | 리뷰 반영: TaskClassifierAgent가 malformed dict를 반환할 경우 전체 파이프라인 크래시 방지 |
| 2026-03-04 | `_analyze_single_file`에 try-except 추가 | 리뷰 반영: Analyzer 예외 시 에러 결과를 반환하여 나머지 파일 분석 계속 |
| 2026-03-04 | Analyzer 인스턴스를 언어별로 캐싱 (`self._analyzers`) | 리뷰 반영: 같은 언어 N개 파일에 N개 인스턴스 생성 방지 |
| 2026-03-04 | `_build_context_map`에서 경로를 `resolve()`로 정규화 | 리뷰 반영: `_validate_and_expand_files`에서 resolve()한 경로와 매칭 보장 |
| 2026-03-04 | `output_dir` 파라미터 제거 | 리뷰 반영: 파일 쓰기는 T14 CLI에서 담당, 미사용 파라미터 제거 |
| 2026-03-04 | 성공 경로 반환에 `errors` 키 추가 | 리뷰 반영: `_empty_result()`와 일관된 반환 구조 |
| 2026-03-04 | OrchestratorAgent import를 `run_analysis` 내부 → 모듈 레벨로 이동 | `patch("mider.main.OrchestratorAgent")` mock이 동작하도록 |
| 2026-03-04 | `MIDER_API_KEY` → `OPENAI_API_KEY` 환경변수 브리징 추가 | 리뷰 반영: LLMClient가 `OPENAI_API_KEY`를 읽으므로 CLI에서 브리징 필수 |
| 2026-03-04 | LLM 에러 감지: 문자열 매칭 → OpenAI 예외 타입 검사 | 리뷰 반영: "api", "connection" 등 문자열 매칭은 false positive 위험 |
| 2026-03-04 | KeyboardInterrupt exit code: 2 → 130 (Unix SIGINT 관례) | 리뷰 반영: 파일 에러(2)와 사용자 취소를 구분 |
| 2026-03-04 | Progress callback `total > 0` 가드 추가 | 리뷰 반영: total==0일 때 `0 >= 0`이 true가 되어 즉시 done 표시 방지 |
| 2026-03-05 | `token_optimizer.py` 신규 유틸리티 (BaseTool 미상속) | 순수 유틸 함수이므로 Tool 인터페이스 불필요 — 4개 Analyzer에서 직접 import |
| 2026-03-05 | Error-Focused: `{file_content}` → `{structure_summary}` + `{error_functions}` | 함수 단위 추출로 토큰 절감 + 논리적 완결성 확보 |
| 2026-03-05 | Heuristic: `{file_content}` → `{file_content_optimized}` (≤500줄 전체, >500줄 축약) | 에러 위치 미상이므로 크기 기반 분기 |
| 2026-03-05 | 중괄호 매칭에 `_count_braces_in_line` 추가 (문자열/주석 무시) | 리뷰 반영: `printf("{")`, `// {` 등에서 함수 경계 오탐 방지 |
| 2026-03-05 | JS 함수 패턴에 제어문 제외 negative lookahead 추가 | 리뷰 반영: `if()/for()` 등이 함수로 오인되는 false positive 방지 |
| 2026-03-05 | Error-Focused fallback에 `optimize_file_content()` 적용 | 리뷰 반영: 에러 블록 추출 실패 시 대형 파일 전체가 프롬프트에 삽입되는 토큰 폭발 방지 |
| 2026-03-05 | `common_patterns` `isinstance(dict)` 타입 가드 추가 | 리뷰 반영: Phase 1에서 비-dict 타입이 들어올 경우 AttributeError 방지 |
| 2026-03-05 | SQL Analyzer 토큰 최적화 제거 — `file_content` 전체 전달 | SQL 파일은 크기가 작고 전체 맥락이 중요하므로 최적화 미적용 |
| 2026-03-05 | DeploymentChecklistGenerator를 BaseTool 상속으로 구현 | ChecklistGenerator와 동일 패턴, LLM 불필요 — 정적 데이터 기반 |
| 2026-03-05 | classify_c_file에서 `"MODULE" in stripped.lower()` → `"module" in stripped.lower()` 버그 수정 | `.lower()`로 변환한 문자열에서 대문자 "MODULE"을 검색하면 영원히 매칭 불가 |
| 2026-03-05 | `.xml` 매핑 제거 (`map_file_to_section`) | Mider `_validate_files`가 `.xml`을 지원하지 않아 도달 불가능한 코드, 리뷰 반영 |
| 2026-03-05 | ReporterAgent 반환 4개 키 (issue_list, checklist, summary, deployment_checklist) | 배포 체크리스트가 4번째 JSON 출력으로 추가 |
| 2026-03-05 | OrchestratorAgent에 `_collect_first_lines` 추가 | C 파일 TP/Module 판별을 위해 첫 줄 읽기 — `.c`/`.h` 파일에만 적용 |
| 2026-03-05 | T18/T19/T15 계획 수립 | SQL 성능개선 강화 + Proframe XML 지원 + 통합 테스트 |
| 2026-03-05 | SQL 통계정보 = Explain Plan 내 Cost/Rows/Bytes | 별도 통계 파일 불필요, Explain Plan 파일 하나로 처리 |
| 2026-03-05 | XML + JS 교차 검증 필요 | XML 이벤트 핸들러(ev:on*)가 대응 JS 파일에 존재하는지 확인 |
| 2026-03-05 | T15를 마지막으로 이동 | T18/T19 완료 후 새 기능 포함하여 E2E 검증 — 이중 작업 방지 |
| 2026-03-05 | ExplainPlanParser `_parse_header`: `(%cpu)` 제거 순서 변경 (replace → strip → replace) | `Cost (%CPU)` → `cost_(%cpu)` → `cost_`로 잘못 변환되어 Cost 컬럼 매핑 실패 |
| 2026-03-05 | ExplainPlanParser `_parse_data_row`: 빈 셀 skip 제거, positional alignment 유지 | Name 빈 셀(`\|        \|`)을 skip하면 이후 컬럼이 밀려 Cost가 Time에 매핑되는 버그 |
| 2026-03-05 | sql_syntax_checker.py: 미사용 import 제거 (Parenthesis, Punctuation, String) | 리뷰 반영 — 코드 정리 |
| 2026-03-05 | orchestrator.py: `_explain_plan_file`을 `__init__`에서 초기화 | 리뷰 반영 — `getattr` 방어 패턴 제거, 명시적 초기화 |
| 2026-03-05 | CHeuristicScanner regex 6종 패턴 + 함수 매핑 구현 | 대형 C 파일(>500줄) 분석 누락 해결 — token 최적화 head/tail로 중간 코드 못잡는 문제 |
| 2026-03-05 | 2-Pass 전략: Pass 1(gpt-4o-mini 선별) → Pass 2(gpt-4o 심층) | 비용 효율 — 전체 파일을 gpt-4o로 보내는 대신 regex로 사전 필터링 |
| 2026-03-05 | `_find_function_boundaries` → `find_function_boundaries` (public) | 리뷰 반영 — 외부 모듈에서 private 함수 import하는 커플링 해소 |
| 2026-03-05 | FORMAT_STRING 패턴에서 `fprintf` 제거 | 리뷰 반영 — fprintf 첫 인자가 FILE*이라 항상 false positive |
| 2026-03-05 | 블록 주석 시작 전 코드도 스캔하도록 수정 | 리뷰 반영 — `int x; /* comment` 줄에서 `int x;` 부분 누락 방지 |
| 2026-03-05 | 2-Pass 경로 `tokens_estimate` 초기값 0으로 선언 | 리뷰 반영 — UnboundLocalError를 제어흐름으로 사용하는 안티패턴 제거 |
| 2026-03-05 | Pass 1 model 전환 시 `fallback_model`도 저장/복원 | 리뷰 반영 — gpt-4o-mini에서 의도치 않은 fallback 방지 |
| 2026-03-06 | Pass 2를 함수별 개별 LLM 호출로 리팩토링 | 대형 함수가 프롬프트를 지배하여 소형 함수 이슈 누락 문제 해결 |
| 2026-03-06 | `_get_lines_for_functions` → `_map_function_boundaries` 변경 (list→dict) | 함수별 개별 호출에서 함수명→시작라인 매핑 필요 |
| 2026-03-06 | asyncio.gather + Semaphore(3)로 병렬 호출 | 비용 동일, 시간 단축 — rate limit 보호 |
| 2026-03-06 | MIDER_EXCLUDE_FUNCTIONS 임시 workaround 제거 | 함수별 개별 호출로 근본 해결되어 불필요 |
| 2026-03-06 | issue_id 재번호 (C-001부터 순차) | 함수별 LLM이 각각 C-001부터 시작하므로 합산 후 재번호 필수 |
| 2026-03-06 | 함수 경계 찾기 실패 시 warning 로그 추가 | 리뷰 반영 — silent skip 방지, 디버깅 가시성 확보 |
| 2026-03-10 | T23 계획 수립 (T18 확장) | ExplainPlan 텍스트 덤프 파싱 검증, SQL 크기 안전장치, 프롬프트 개선, E2E 테스트 |
| 2026-03-10 | ExplainPlanParser 텍스트 덤프 파싱 단위 테스트 55개 추가 | 기존 구현(62a0ae8)의 검증 — `_is_text_dump`, `_parse_text_dump`, `_parse_operation_detail`, `_format_as_xplan_table`, `_is_operation_line` |
| 2026-03-10 | SQL 대형 파일 안전장치: 토큰 추정 로깅 + 100K 초과 warning | FileReader는 잘림 없으나, 향후 LLM context 초과 방어 |
| 2026-03-10 | 프롬프트 개선: Explain Plan → 인덱스 힌트 유도 지시 추가 | LLM이 TABLE ACCESS FULL 탐지 시 `/*+ INDEX(alias (column)) */` 같은 구체적 힌트 제안하도록 |
| 2026-03-10 | E2E 테스트 성공: gpt-4o-mini가 4개 이슈 탐지, `/*+ INDEX(b (svc_prod_grp_id)) */` 구체적 힌트 제안 | 프롬프트 개선 효과 확인 — 이전에는 인덱스 힌트 미생성 |
| 2026-03-10 | SQL Analyzer 기본 모델 gpt-4o-mini → gpt-4o 변경 | gpt-4o-mini는 PK 인덱스 비효율 패턴을 DBA 수준으로 추론 불가 (이슈 #004) |
| 2026-03-10 | gpt-4o E2E 테스트: 6개 이슈, 인덱스 힌트 포함 확인 | gpt-4o가 `(chld_svc_mgmt_num, svc_mgmt_num)` 힌트 제안 성공 |
| 2026-03-10 | CLI 테스트에서 인덱스 힌트 누락 확인 → LLM 비결정성 문제 | 수동 테스트에서 나왔지만 CLI에서 안 나옴 — 근본 해결 필요 (T24 계획) |

## T24 설계 결정 (Explain Plan 정적 이슈 자동 생성)
- **문제**: LLM이 튜닝 포인트를 이슈로 변환하는 것이 비결정적 — 같은 입력이라도 결과가 달라짐
- **해결**: HIGH/CRITICAL 튜닝 포인트를 LLM 없이 직접 이슈로 생성, LLM 이슈와 병합
- **이슈 생성 위치**: SQLAnalyzerAgent (Tool이 아닌 Agent에서 이슈 형식 생성)
- **병합 규칙**: 같은 object 이름이 LLM 이슈에도 있으면 LLM 우선 (더 상세), 없으면 정적 이슈 추가
- **대상 튜닝 포인트**: CRITICAL (CARTESIAN), HIGH (PK 인덱스 고비용, TABLE ACCESS FULL 고비용)
- **인덱스 접미사 매칭**: `_PK`, `_N1`, `_U1` 등 접미사를 제거하여 베이스 테이블명으로도 중복 판정 (리뷰 중 발견)

| 2026-03-10 | `_generate_static_issues()` + `_merge_issues()` 구현 | LLM 비결정성 근본 해결 — 정적 이슈가 LLM 누락을 보충 |
| 2026-03-10 | 인덱스 접미사 제거 매칭: `ZORD_WIRE_SVC_DC_PK` → `ZORD_WIRE_SVC_DC` | `_PK` 접미사가 있으면 LLM 텍스트의 테이블명과 매칭 실패 — 테스트 실패로 발견 |
| 2026-03-10 | `/*+` 힌트 추출 시 `*/` 존재 여부 확인 추가 | 리뷰 H2: `*/` 없는 비정상 suggestion 시 ValueError crash 방지 |
| 2026-03-10 | `high_cost_ids` dead code 제거 | 리뷰 H1: 미사용 변수 정리 |
| 2026-03-10 | `fallback_model=None` (기본 모델과 동일하면 불필요) | 리뷰 H3: gpt-4o → gpt-4o fallback은 실질적 효과 없음 |
| 2026-03-10 | `__main__.py`에 `if __name__ == "__main__":` 가드 추가 | 리뷰 M3: import 시 의도치 않은 CLI 실행 방지 |
| 2026-03-10 | XMLParser: `dl.iter()` 재귀 탐색으로 변경 | 실제 WebSquare XML에서 columnInfo 래퍼가 있어 직접 자식만 탐색하면 column 0개 반환 |
| 2026-03-10 | `_extract_handler_functions()`: `scwin\.(\w+)` 패턴으로 변경 | 실제 XML에서 `ev:onclick="scwin.func_name"` 형태로 괄호 없이 사용 |
| 2026-03-10 | column에 `name` 속성 추출 추가 | 한국어 컬럼명이 분석에 유용 — 사용자 요청 |
| 2026-03-10 | XXE/Billion Laughs 방어: DOCTYPE/ENTITY 선언 포함 XML 거부 | 리뷰 반영 — defusedxml 미사용(폐쇄망 의존성 최소화), Python 3.13 readonly entity 속성 |
| 2026-03-10 | 핸들러 검증: substring → `re.search(rf"\b{re.escape(func_name)}\b")` | 리뷰 반영 — 부분 문자열 매칭 false negative 방지 |
| 2026-03-10 | PatternInfo Literal에 `event_binding` 추가 | 리뷰 반영 — XML 이벤트 바인딩을 error_handling으로 잘못 분류하던 문제 |
| 2026-03-10 | 미사용 `_NS` 딕셔너리 제거, 불필요한 `js_content` 반환 제거 | 리뷰 반영 — dead code 정리 |
| 2026-03-10 | 이슈 #005 기록: XML `<script>` 미추출 및 토큰 비효율 | 실제 XML 테스트에서 발견 — 별도 Task로 분리 |

## T25 설계 결정 (XML 중복 ID 스코프 개선)
- **문제**: `_extract_component_ids`가 `<w2:column>` 등 데이터 정의 요소의 id도 수집 → 서로 다른 dataList 간 동명 컬럼이 중복으로 오탐
- **실제 사례**: `ZORDSS03S0100.xml`에서 `DS_REQR_INFO`와 `DS_FAX_INFO`의 `req_sale_org_id`가 중복 보고
- **해결**: 데이터 정의 내부 요소(`column`, `columnInfo`, `data`)를 컴포넌트 ID 수집에서 제외
- **`dataList`/`dataMap` ID는 유지**: WebSquare에서 `$w.getById("dlt_search")`로 접근하는 document-level ID이므로 중복 검사 대상
- **제외 태그 상수화**: `_DATA_DEFINITION_TAGS` 세트로 관리 → 향후 추가 태그 지원 용이

| 2026-03-17 | `_extract_component_ids`에서 column/columnInfo/data 태그 제외 | 데이터 정의 요소 id는 DOM 컴포넌트가 아니므로 중복 검사 대상 아님 (이슈 #005 Phase 3) |
| 2026-03-17 | gpt-5/gpt-5-mini 업그레이드 + settings_loader.py 도입 | Agent별 모델 하드코딩 제거, settings.yaml 중앙 관리 |
| 2026-03-17 | gpt-5 계열 temperature 파라미터 생략 | gpt-5는 기본값(1)만 지원 — llm_client.py에서 자동 처리 |
| 2026-03-17 | 중복 ID 라인 번호 추출 (`_find_id_lines`) | LLM이 정확한 location.line_start 제공하도록 |
| 2026-03-17 | 단일 파일 Phase 0/1 LLM skip | 파일 1개일 때 우선순위 보정/컨텍스트 보정 불필요 — LLM 2회 호출 절감 |

## T26 설계 결정 (Agent 추론 로그 시각화)
- **목적**: Agent의 사고 과정(Planning, Tool Call, LLM Call, Self-Correction)을 CLI에 실시간 표시
- **구현 위치**: `ReasoningLogger` 유틸 (Rich Console 기반, 기존 logging과 분리)
- **기존 logging과의 관계**: `logging.DEBUG`는 개발자용 상세 로그, ReasoningLogger는 사용자 facing 고수준 로그
- **verbose 모드**: `-v` 옵션일 때 상세 추론 로그 표시, 기본은 Phase 진행률만

| 2026-03-17 | ReasoningLogger 유틸 구현 (reasoning_logger.py) | Agent 추론 과정을 컬러 dot으로 CLI 시각화 — 기존 logging과 분리 |
| 2026-03-17 | BaseAgent에 `rl` 속성 추가 (기본 no-op) | 모든 Agent가 ReasoningLogger에 접근, verbose=False이면 오버헤드 없음 |
| 2026-03-17 | OrchestratorAgent에서 Sub-Agent에 rl 전달 | `agent.rl = self.rl` 패턴으로 주입 전파 |
| 2026-03-17 | XMLAnalyzerAgent 상세 추론 로그 | 파서→경로선택→프롬프트→LLM→후처리 전 과정 시각화 |
| 2026-03-17 | 리뷰 반영: reporter reason elif 분기 | CRITICAL+HIGH 동시 시 중복 차단 문구 방지 (M2) |
| 2026-03-17 | 리뷰 반영: context scan 로그에 파일명 추가 | 어떤 파일의 scan 결과인지 식별 가능 (L2) |

## T27 설계 결정 (clang-tidy 헤더 누락 fallback)
- **문제**: clang-tidy가 실행은 되지만 헤더 에러(fatal error: file not found)만 발생 → 유의미한 경고 0개인데 Error-Focused 경로 진입 → LLM에 쓸모없는 에러만 전달
- **해결**: `_run_clang_tidy()`에서 헤더 관련 에러를 필터링, 유의미한 경고만 남김. 0건이면 None 반환 → Heuristic/2-Pass fallback
- **헤더 에러 판정 기준**: severity="error" + 메시지에 `file not found`, `unknown type name`, `use of undeclared identifier`, `no such file or directory` 포함
- **clang-diagnostic-error 과도한 일반화 방지**: check 이름만으로 판정하지 않고 반드시 메시지 키워드 확인 (구문 에러 `expected ';'` 등은 유의미하므로 필터링 방지)
- **유의미 경고 + 헤더 에러 혼재 시**: 유의미 경고만 남기고 Error-Focused 유지 (이슈 #002 방안 1의 축소 적용)

| 2026-03-17 | `_is_header_error()` + `_run_clang_tidy()` 헤더 에러 필터링 | 헤더 에러만 있으면 Error-Focused가 무의미 → Heuristic/2-Pass fallback (이슈 #006) |
| 2026-03-17 | 리뷰 반영: clang-diagnostic-error 메시지 키워드 확인 필수 | 구문 에러(expected ';')도 clang-diagnostic-error이므로 check만으로 판정하면 유의미 에러 필터링됨 |
| 2026-03-17 | 리뷰 반영: dead entry `'included' file not found` 제거, `no such file or directory` 추가 | 실제 clang 메시지와 매칭되지 않는 키워드 정리 |

## T28 설계 결정 (clang-tidy Level 1 저가치 필터링)
- **문제**: T27에서 헤더 에러만 필터링했으나, Level 1(bugprone-*) 44건이 "유의미"로 통과 → Error-Focused에서 LLM이 44건을 개별 이슈로 번역 (285초, 94K tokens)
- **실제 사례**: 2932줄 C 파일, clang-tidy 45건 중 헤더 에러 1건 제거 → 44건 Level 1 → 48개 이슈 중 45건이 노이즈
- **해결**: 헤더 에러가 1건이라도 있으면 Level 1도 저가치로 분류. `clang-analyzer-*`(Level 2)만 유의미
- **Level 2 판정 기준**: check 접두사가 `clang-analyzer-` (데이터 흐름 분석, AST 완성 필요)
- **Level 1 판정 기준**: `bugprone-*`, `cert-*`, `misc-*` 등 나머지 (텍스트/구문 패턴)
- **헤더 에러 없을 때**: 기존 동작 유지 (Level 1 포함 — AST 완성 시 Level 1도 유의미)

## T30 설계 결정 (Pro*C Heuristic Scanner)
- **목적**: 실제 장애 유발 패턴 3+1종을 regex로 사전 스캔 → LLM에 집중 분석 요청
- **아키텍처**: C의 CHeuristicScanner + 2-Pass 전략을 Pro*C에 적용
- **패턴 4종**: FORMAT_STRUCT(%s에 구조체), MEMSET_SIZEOF_MISMATCH, LOOP_INIT_MISSING, FCLOSE_MISSING
- **Scanner 위치**: `mider/tools/static_analysis/proc_heuristic_scanner.py`
- **연동**: ProCAnalyzerAgent에서 Scanner 결과 > 0이면 Error-Focused 강제 진입
- **프롬프트**: 장애 사례 few-shot으로 LLM 판정 정확도 향상

## T31 설계 결정 (CAnalyzer 통합 개선, T22 흡수)
- **문제 1 (2-Pass 블라인드 스팟)**: regex 히트 함수만 Pass 1에 전달 → 미히트 함수의 결함 누락
- **문제 2 (경로 분리)**: clang-tidy 있으면 regex 안 돌림, ≤500줄이면 regex 안 돌림 → 탐지 기회 상실
- **해결**: CHeuristicScanner를 **모든 경로에서 항상 실행**, 결과를 각 경로별 프롬프트에 추가 전달
  - Error-Focused: clang-tidy 경고 + regex findings 병합 (기존 T22 흡수)
  - Heuristic (≤500줄): 전체 코드 + regex findings
  - 2-Pass (>500줄): 전체 함수 시그니처 + regex findings → gpt-5-mini 선별
- **추가 토큰 비용**: regex findings ~10줄, 함수 시그니처 ~40줄 → gpt-5-mini 비용 무시 가능
- **중복 제거 (Error-Focused)**: clang-tidy 경고와 regex findings 동일 라인(±2) + 동일 카테고리 → clang-tidy 우선

| 2026-03-25 | 5개 Analyzer에 분석 경로/도구 결과/후처리 표준 logger.info 추가 | ReasoningLogger(verbose=True만)에만 있던 정보를 표준 로그에도 출력 — 로그 파일에서 에이전트별 동작 차이 확인 가능 |
| 2026-03-25 | 리뷰: `_fn`/`filename` 이중 선언 → `filename` 1회로 통합 (proc/sql/xml) | 리뷰 반영 — 같은 메서드 내 동일 값 중복 계산 제거 |
| 2026-03-25 | 리뷰: `_tp_count`/`tuning_count` 중복 → `tuning_count` 1회로 통합 (sql) | 리뷰 반영 — 동일 expression 중복 계산 제거 |
| 2026-03-25 | 리뷰: 루프 변수 `f` → `finding` (c_analyzer Scanner 집계) | 리뷰 반영 — f-string 접두사와 혼동 방지 |

| 2026-03-25 | T31 구현: `build_all_functions_summary()` 추가 (token_optimizer.py) | 2-Pass Pass 1에서 LLM이 전체 함수 목록을 파악하여 regex 미히트 함수도 선별 가능 |
| 2026-03-25 | T31 구현: Scanner를 `run()` 시작부에서 항상 실행 | 모든 경로(Error-Focused, Heuristic, 2-Pass)에서 regex 결과 확보 |
| 2026-03-25 | T31 구현: >500줄 → 항상 2-Pass (clang 유무 무관) | clang-tidy 있어도 대형 파일에서 Error-Focused만 하면 clang 미탐지 영역 누락 |
| 2026-03-25 | T31 구현: Error-Focused/Heuristic에 scanner_findings 병합 | clang 경고만으로 놓치는 UNINIT_VAR, UNSAFE_FUNC 등을 regex가 보충 |
| 2026-03-25 | T31 구현: 2-Pass에 clang 경고 통합 (Pass 1 선별 + Pass 2 함수별 전달) | 대형 파일에서 clang + scanner 양쪽 정보를 LLM에 전달하여 정밀도 향상 |
| 2026-03-25 | T31: Level 1(bugprone) 저가치 필터링 제거 | StubHeaderGenerator가 기본 타입을 제공하므로 Level 1도 신뢰 가능 → 헤더 에러만 제거, 나머지 유지 |
| 2026-03-25 | T31: `_is_level2_warning()` + `_LEVEL2_CHECK_PREFIX` 제거 | Level 1/2 구분 로직이 더 이상 필요 없음 — dead code 정리 |

| 2026-03-25 | c_prescan_fewshot.txt few-shot 예시를 실제 장애 사례로 교체 (placeholder → 실제) | 사용자 제공 장애 데이터 기반 — UNINIT_VAR(루프 미초기화), BOUNDED_FUNC(memset sizeof 불일치), FORMAT_STRING(%s에 long 전달) 위험 3건 + 안전 2건 |

## T33 설계 결정 (ProC 함수별 청킹 + 커서 맵 + 2-Pass)
- **문제 1 (Error-Focused 사각지대)**: 에러 없는 함수의 로직 결함 누락 — Error-Focused는 에러 함수만 LLM에 전달
- **문제 2 (Heuristic 중간 누락)**: >500줄 파일에서 head 200 + tail 100만 전달 → 중간 코드 분석 불가 (8000줄이면 96% 누락)
- **문제 3 (커서 크로스레퍼런스)**: DECLARE→OPEN은 b10, FETCH는 b20에 분산 → 함수별 분석 시 CLOSE 누락을 못 잡음
- **문제 4 (비용)**: 7958줄/111함수를 전부 gpt-4o로 호출하면 비용/시간 과다
- **해결**: 함수별 청킹 + 커서 라이프사이클 맵 + 2-Pass 선별
  - 커서 맵: regex로 DECLARE/OPEN/FETCH/CLOSE 위치+함수 추적 (비용 0)
  - 2-Pass: mini로 위험 함수 선별 → primary로 개별 함수 분석
  - C analyzer의 `_run_two_pass()` + `_analyze_single_function()` 패턴 재사용
- **전략 비교 (호출 그래프 기반 그룹핑 기각)**: 그룹이 커지면 Issue #003 "대형 함수 압도" 문제 재발. b10(1204줄)+b20(144줄)+b30(138줄) 그룹 = 1486줄 → 소형 함수 누락 위험
- **함수별 청킹의 장점**: 평균 72줄/함수(sample_inv 기준), 개별 LLM attention 분산 없음
- **진행률 로그**: 25% 단위 4회 출력 — 111함수 개별 로그는 과다, 사용자가 진행 상황 확인 가능

| 2026-03-31 | T33 구현: `extract_proc_global_context()` + `build_cursor_lifecycle_map()` 신규 (token_optimizer.py) | 함수별 청킹 시 글로벌 변수/커서 추적에 필수 |
| 2026-03-31 | T33 구현: SQLExtractor에 `function` 필드 추가 | 함수별 SQL 블록 필터링 위해 — `_find_enclosing_function()` 패턴 |
| 2026-03-31 | T33 구현: `_run_function_chunked()` 2-Pass (mini 선별 → 함수별 LLM) | C analyzer `_run_two_pass()` 패턴 재사용, 함수 ≥2 AND >500줄 조건 |
| 2026-03-31 | T33 구현: 기존 Error-Focused/Heuristic 분기는 ≤500줄 파일에서 유지 | 소형 파일은 청킹 오버헤드만 증가 |
| 2026-03-31 | T33 리뷰: proc_analyzer_function.txt `{{{{` → `{{` 수정 | 이중 에스케이프로 LLM에 `{{ }}` 전달 → JSON 파싱 실패 (CRITICAL) |
| 2026-03-31 | T33 리뷰: 진행률 로그 `if` → `while` 변경 | 마일스톤 건너뛰기 방지 (HIGH) |
| 2026-03-31 | T33 리뷰: `__import__("re")` → `import re` | 컨벤션 위반 (MEDIUM) |
| 2026-03-31 | T33 리뷰: typedef/struct 함수 내부 필터링 추가 | 함수 안 typedef가 글로벌 컨텍스트에 포함되는 버그 방지 (MEDIUM) |

## T33 재설계 결정 (전체 코드 전달 + 스마트 그룹핑)
- **기존 T33 문제**: 함수별 개별 분석 → Scanner가 못 잡는 버그를 LLM도 못 잡음 (에러 함수만 추출)
- **핵심 변경**: Error-Focused/Heuristic 분기 제거 → 전체 코드를 LLM에 직접 전달
- **토큰 한계 대응**: 24개 샘플 중 22개는 단일 호출 가능, 2개(~130K~176K tokens)만 그룹핑 필요
- **스마트 그룹핑 (대형 파일)**: ProC 함수 패턴 분석 결과 3가지 분류
  - 계층형(b10+b20+b30): 커서/변수 흐름 공유 → 형제 그룹핑
  - 디스패치형(work_proc1~11): 독립적 → 개별 분석
  - 유틸(z+s계열): 접두사별 그룹핑
- **Pass 1 역할 변경**: 위험 함수 "선별" → 위험 함수 "태깅" (전체 분석하되 중점 표시)
- **기존 유틸 재사용**: 글로벌 컨텍스트, 커서 맵, SQL 함수 매핑, Pass 1 프롬프트

| 2026-03-31 | T33 재설계: proc_analyzer.txt 통합 프롬프트 (error_focused+heuristic 2개→1개) | 전체 코드 전달이므로 분기 불필요 |
| 2026-03-31 | T33 재설계: classify_proc_functions 함수 패턴 분류기 | 계층형/디스패치형/유틸/보일러플레이트 자동 분류 |
| 2026-03-31 | T33 재설계: _decide_delivery_mode (100K 토큰 기준 분기) | 128K 한계에서 프롬프트+응답 여유분 확보 |
| 2026-03-31 | T33 재설계: _run_single_call — 전체 코드 단일 호출 | Scanner 한계 해결: LLM이 전체 코드에서 직접 버그 탐지 |
| 2026-03-31 | T33 재설계: _run_grouped_call — 스마트 그룹핑 | 대형 파일(>100K tok) Lost-in-the-Middle 최소화 |
| 2026-03-31 | T33 리뷰: json.loads에 try-except 추가 3곳 | LLM 응답 파싱 실패 시 graceful degradation (CRITICAL) |
| 2026-03-31 | T33 리뷰: 진행률 카운터에 asyncio.Lock 추가 | 병렬 태스크 간 중복 마일스톤 로그 방지 (CRITICAL) |
| 2026-03-31 | T33 리뷰: proc_analyzer_function.txt 삭제 | 미사용 dead code (HIGH) |

| 2026-03-31 | T34 구현: XMLParser에 extract_inline_scripts + ScriptBlock + js_line_to_xml_line | 인라인 JS(파일의 78%)를 추출하여 분석 가능하게 |
| 2026-03-31 | T34 구현: build_datalist_summary (45K→1.5K 토큰, 97% 절감) | dataList 전체 JSON은 토큰 낭비, 이름+컬럼수 요약으로 충분 |
| 2026-03-31 | T34 구현: XML Analyzer 재구조화 — 인라인 JS를 JS Analyzer에 위임 | JS 분석 파이프라인(ESLint + Few-Shot) 재사용, 코드 중복 방지 |
| 2026-03-31 | T34 구현: XML 프롬프트 2개→1개 통합, Error-Focused/Heuristic 제거 | T32 JS와 동일한 단순화 |
| 2026-03-31 | T34 리뷰: tempfile.mktemp → NamedTemporaryFile | TOCTOU race condition 방지 (HIGH) |
| 2026-03-31 | T34 리뷰: 한 줄짜리 CDATA offset_map 누락 수정 | offset_map에 등록 안 되면 라인 매핑 실패 (HIGH) |
| 2026-03-31 | T34 리뷰: import re 함수 내부 → 파일 상단 이동 | 컨벤션 위반 (MEDIUM) |

| 2026-04-09 | T44 구현: get_base_dir() + resolve_input_files() 추가 | PyInstaller frozen 환경에서 input 폴더 기준 파일 해석 필요 |
| 2026-04-09 | T44 구현: main()에서 .env 로드 경로를 base_dir 기준으로 변경 | 실행파일과 같은 폴더의 .env를 읽어야 함 |
| 2026-04-09 | T44 구현: build_parser()에 output_default 인자 추가 | --output 기본값을 base_dir/output으로 동적 설정 필요 |
| 2026-04-09 | T45 구현: mider.spec onedir 모드로 PyInstaller 설정 | onefile은 시작 느림, onedir이 폐쇄망 배포에 적합 |
| 2026-04-09 | T45 구현: scripts/build_exe.py 빌드 후 dist 폴더 자동 구성 | input/output 폴더 + .env.example 자동 배치 |
| 2026-04-09 | T46 구현: docs/USER_MANUAL.md 폐쇄망 운영자 대상 한국어 매뉴얼 | 비개발자도 사용할 수 있는 상세 가이드 필요 |

## T35 설계 검토 사항
- **주석 처리**: 제거 시 라인번호 깨짐 CRITICAL, 3~20% 토큰 절감 — 선택적 제거(헤더 주석만) 또는 현행 유지 권장

## T44~T46 설계 결정 (배포용 실행파일 환경)
- **get_base_dir()**: `getattr(sys, 'frozen', False)`로 PyInstaller 환경 판별, frozen이면 `sys.executable.parent`, 아니면 프로젝트 루트
- **resolve_input_files()**: 절대경로/존재하는 상대경로는 통과, 나머지는 `base_dir/input/` 기준으로 해석
- **onedir 모드**: PyInstaller onefile은 임시 폴더에 풀리므로 input/output 폴더 접근 불가, onedir이 적합
- **.env 경로**: `load_dotenv(dotenv_path=base_dir / '.env')`로 실행파일 옆의 .env를 명시적으로 로드

## T47~T49 설계 결정 (AICA API 전환)
- **openai SDK 제거**: AICA API는 OpenAI 호환이 아닌 자체 프로토콜 사용 — httpx로 직접 호출
- **AICA API 스펙**:
  - Endpoint: `/api/agent/v1/chats` (POST)
  - Request: `user_id`, `message`, `model_cd`, `usecase_mode("GENERAL")`, `stream(false)`
  - Response: `token.data` (LLM 응답 텍스트), `error` (에러 객체)
  - Headers: `X-AGENT-API-KEY`, `Cookie: SSOSESSION=xxx`
- **model_cd 매핑**: settings.yaml 모델명 → AICA 모델코드 (gpt-5 → GPT5_2 등)
- **SSO 세션**: 별도 작업에서 구현, LLMClient는 SSOSESSION 쿠키 전달 인터페이스만 준비
- **환경 변수 단순화**: Azure 3종(KEY+ENDPOINT+VERSION) + OpenAI 2종 → AICA_API_KEY + AICA_ENDPOINT
- **서버 환경**: STG(aicas.sktelecom.com:3000), PRD(aica.sktelecom.com:3000)
- **에러 처리**: status_code 50011(한도 오류), 50012(개인정보 검출) 대응 필요

## T50~T52 설계 결정 (SSO 인증 연동)
- **SSOAuthenticator 별도 모듈**: `mider/config/sso_auth.py` — LLMClient와 분리하여 단일 책임 유지
- **selenium optional import**: `try: from selenium import webdriver` 패턴, 미설치 시 ImportError 안내
- **세션 캐싱**: JSON 파일 (issued_at, sso_session, user_id, name), 1시간 TTL
- **만료 감지**: AICA 응답이 `text/html` 또는 `<` 시작 → SSO 리다이렉트로 판단
- **자동 재인증**: 만료 시 1회만 재시도, 무한 루프 방지
- **`app_env` 필드**: 데모 스크립트의 `"app_env": "prd"` — llm_client.py payload에 추가 필요
- **인터랙티브 `login` 명령어**: `--sso` 플래그 제거 → 프롬프트에서 `login` 입력으로 SSO 로그인. 시작 시 세션 없으면 안내 메시지 출력
- **chromedriver 경로**: settings.yaml SSO 섹션 + `CHROME_DRIVER_PATH` 환경변수 override
- **GUI 필수**: Selenium 브라우저 로그인은 GUI 환경 필요, headless CI에서는 환경변수 fallback
- **`.sso_session.json` 보안**: `.gitignore`에 추가 필수 (민감 정보)

| 2026-04-13 | SSO 연동 계획 수립 (T50~T52) | 사용자 요청: 데모 스크립트 기반 SSO 인증 자동화 통합 |
| 2026-04-13 | **BUG 발견**: `_chat_aica()` 응답 파싱 오류 — `token.data` → `choices[0].message.content` | 실제 AICA 응답이 OpenAI 호환 형식(`choices[].message.content`)인데 `token.data`로 파싱 중이었음 |
| 2026-04-13 | payload에 `app_env: "prd"` 필드 추가 필요 | 데모 스크립트 request/response 확인 — 현재 llm_client.py에 누락 |
| 2026-04-13 | SSO user_id를 payload에 전달하도록 변경 | AICA API가 실제 사번을 `user_id`로 요구 — 기존 "mider_agent" 하드코딩 대체 |
| 2026-04-13 | T53 추가: `input/` 폴더 제거 + `base_dir` rglob 검색 | ProFrame workspace에서 파일이 서브디렉토리(AATDD069261CN/ 등)에 위치 — input 복사 불필요, 파일명만 입력하면 자동 탐색 |
| 2026-04-13 | T50 완료: 리뷰 반영 — 세션 파일 chmod 0o600, auth_data keys()만 노출, None 응답 테스트 추가 | 코드 리뷰에서 보안 이슈 3건(HIGH) 지적 — 세션 파일 퍼미션, 민감 정보 로그 노출, 네트워크 타임아웃 케이스 |
| 2026-04-13 | T51 완료: `_chat_aica()` 응답 파싱 `token.data` → `choices[0].message.content` | 실제 AICA 응답이 OpenAI 호환 형식 — 기존 파싱 완전히 틀렸음 |
| 2026-04-13 | T51: `_chat_aica()` → `_chat_aica_once()` 분리 + SSO 만료 시 1회 재시도 | HTML 응답 감지로 세션 만료 판단 → SSOAuthenticator force_login → 재시도 |
| 2026-04-13 | T51 리뷰: `sso_authenticator: object` → `SSOAuthenticator` (TYPE_CHECKING) | duck typing으로 AttributeError 위험 — 정적 타입 체크 활성화 |
| 2026-04-13 | T51 리뷰: `_is_sso_expired_response()`에 application/json early return | 정상 JSON 응답에서 불필요한 text 파싱 방지 — 대형 응답 성능 개선 |
| 2026-04-13 | T52 설계 변경: `--sso` 플래그 → 인터랙티브 `login` 명령어 | 사용자 피드백: 매번 `--sso` 붙이는 것보다 프롬프트에서 `login` 입력이 직관적. 시작 시 세션 없으면 자동 안내 |

## T54 설계 결정 (C Analyzer 스마트 라우팅)
- **문제**: 874줄 C 파일 분석에 160초 소요. 2-Pass(Pass1 mini + Pass2 함수별 N회)로 LLM 호출 5~6회. 호출당 TTFT 15초 병목
- **해결**: `line_count > 500` 하드코딩 → 토큰 추정(len//3) + 함수 크기 균일성(max/median ≤ 5배) 기반 라우팅
- **토큰 한계**: 100K (Pro*C `_TOKEN_LIMIT`과 동일). 128K context - 28K 여유
- **함수 크기 균일성**: 최대/중앙값 > 5배 → 편차 큼 → per_function. T21의 636줄 vs 127줄 = 5배 경계
- **비용보다 속도+정확도 우선**: 사용자 명시 요구

| 2026-04-13 | T54: `line_count > 500` → `_decide_c_delivery_mode()` 토큰+함수크기 기반 라우팅 | 874줄 파일 160초 → ~40초 목표. 균일 파일은 single(1회), 편차 큰 파일만 per_function(N회) |
| 2026-04-13 | T54 리뷰: docstring 업데이트 + `language` 파라미터 전달 + mojibake 수정 | MEDIUM 2건 + LOW 2건 반영 |

## T55 설계 결정 (memset sizeof 타입 불일치 탐지)
- **발견 계기**: `zordms03s0200.c:272`에서 `memset(&u0010_in, 0, sizeof(s0009_in_t))` 복붙 버그 미탐지
- **Scanner regex 전략**: 변수명에서 접미사(`_in`, `_out`, `_io`, `_ar`) 제거 → `_t` 추가 → sizeof 타입과 비교. 불일치면 `MEMSET_SIZE_MISMATCH`
- **프롬프트 보완**: Scanner만으로는 `ctx->member` 같은 간접 접근을 못 잡으므로 LLM 프롬프트에 체크 항목 + few-shot 예시 추가
- **구조체 멤버 제외**: `ctx->var` 형태는 변수명만으로 타입 추론 불가 → Scanner에서 제외, LLM에 위임

| 2026-04-13 | T55 계획: memset sizeof 타입 불일치 탐지 (Scanner + 프롬프트) | zordms03s0200.c에서 u0010 변수에 s0009 타입 sizeof 사용하는 복붙 버그 미탐지 |
| 2026-04-13 | T55: Scanner `MEMSET_SIZE_MISMATCH` + LLM 프롬프트 양쪽 보강 | 정적 탐지(높은 정밀도) + LLM 문맥 탐지(높은 재현율) 보완 |
| 2026-04-13 | T55: `ll_` 접두사 오탐 발견 → 로컬 접두사 제거 로직 추가 | L805 `ll_zngmmmsg12310_io`가 오탐 — ProFrame 네이밍 규칙 반영 |
| 2026-04-13 | T55 리뷰: regex `^[ld][lcds]_` → `^l[lcds]_` | `d`접두사 과도 매칭 방지 (HIGH). `_HIGH_PRIORITY_PATTERNS`에 추가 (MEDIUM) |
| 2026-04-13 | T56: `prompt_for_explain_plan()` 추가 — SQL 감지 시 인터랙티브 질문 | `--explain-plan` CLI 플래그는 exe 인터랙티브 모드와 UX 불일치. 자동 질문으로 해결 |
| 2026-04-13 | T56 리뷰: main.py 구버전 함수 중복 정의 제거 + 테스트 중복 클래스 제거 | 머지 시 유입된 dead code (HIGH 2건) |

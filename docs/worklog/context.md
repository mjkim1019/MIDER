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

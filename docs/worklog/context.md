# 맥락 노트

## 설계 결정
- Bottom-up 구현 순서: 기반 → 스키마 → 인프라 → Tool → Agent → CLI → 통합
- T4/T6/T7/T8 병렬 구현: 모두 T3(Base Infrastructure)만 의존하므로 독립적
- T11/T12 병렬 구현: Phase 2 Analyzer와 Phase 3 Reporter는 스키마가 확정되어 있으므로 병렬 가능
- LSP Tool (T7)은 1차 PoC에서 선택적 기능 — 바이너리 없을 시 graceful degradation
- ContextCollectorAgent는 Tool 기반 추출 + LLM 보정 하이브리드 방식 채택 (TaskClassifierAgent 패턴 동일)

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
- 1차 PoC 범위: RAG, Session Resume, Context 압축 제외
- print() 금지 → rich/logging 사용
- Agent는 코드 수정 불가 (제안만)
- Before/After 코드는 1-3줄만
- `**kwargs` 남용 금지 — 명시적 파라미터 사용 (TaskClassifierAgent에서 이미 적용됨)

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

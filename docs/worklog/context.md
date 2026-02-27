# 맥락 노트

## 설계 결정
- Bottom-up 구현 순서: 기반 → 스키마 → 인프라 → Tool → Agent → CLI → 통합
- T4/T6/T7/T8 병렬 구현: 모두 T3(Base Infrastructure)만 의존하므로 독립적
- T11/T12 병렬 구현: Phase 2 Analyzer와 Phase 3 Reporter는 스키마가 확정되어 있으므로 병렬 가능
- LSP Tool (T7)은 1차 PoC에서 선택적 기능 — 바이너리 없을 시 graceful degradation

## 참조 문서
- docs/TECH_SPEC.md: Agent 워크플로우 전체 (섹션 2)
- docs/DATA_SCHEMA.md: Pydantic 스키마 정의 (섹션 1-4)
- docs/CLI_SPEC.md: CLI 옵션, 터미널 출력 형식
- docs/manuals/agents.md: BaseAgent 패턴, call_llm() 재시도
- docs/manuals/tools.md: BaseTool 인터페이스

## 주의사항
- 1차 PoC 범위: RAG, Session Resume, Context 압축 제외
- print() 금지 → rich/logging 사용
- Agent는 코드 수정 불가 (제안만)
- Before/After 코드는 1-3줄만

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

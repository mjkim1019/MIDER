# 셀프 리뷰 체크리스트

> Task 완료 전 반드시 아래 항목을 점검하세요.

## 코드 품질
- [ ] 모든 함수에 타입 힌트가 있는가?
- [ ] print() 대신 rich/logging을 사용했는가?
- [ ] 에러 처리가 적절한가? (try-except, ToolExecutionError)
- [ ] 하드코딩된 값이 없는가? (API 키, 파일 경로 등)
- [ ] import 순서가 올바른가? (stdlib → third-party → local)

## 스키마 일치
- [ ] DATA_SCHEMA.md의 스키마와 일치하는가?
- [ ] Pydantic v2 문법을 사용했는가? (model_validate, model_dump)
- [ ] 필수 필드가 모두 포함되어 있는가?

## Agent 규칙
- [ ] Agent가 코드를 직접 수정하지 않는가? (제안만)
- [ ] LLM 호출 시 재시도 로직이 있는가? (최대 3회)
- [ ] JSON Mode 출력을 사용하는가?

## 보안
- [ ] API 키가 코드에 하드코딩되지 않았는가?
- [ ] 사용자 입력에 대한 검증이 있는가?
- [ ] 민감 정보가 로그에 출력되지 않는가?

## 테스트
- [ ] 새로운 기능에 대한 테스트가 있는가?
- [ ] LLM 호출은 mock 처리되었는가?
- [ ] 경계값/에러 케이스 테스트가 있는가?

## 문서
- [ ] checklist.md에서 완료한 Subtask를 [x]로 변경했는가?
- [ ] 설계 변경이 있었다면 context.md에 기록했는가?

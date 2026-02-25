#!/bin/bash
# 셀프 체크 리마인더 (Stop Hook)
# Claude가 작업을 멈출 때 자동으로 체크 항목을 보여준다.

cat << 'REMINDER'
[셀프 체크 리마인더] 작업 종료 전 아래 항목을 확인하세요:
- 타입 힌트가 모든 함수에 있는가?
- 에러 처리가 누락되지 않았는가?
- print() 대신 logging/rich를 사용했는가?
- 하드코딩된 값이 없는가?
- docs/worklog/checklist.md를 업데이트했는가?
REMINDER

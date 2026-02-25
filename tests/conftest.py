"""Mider 테스트 공통 fixture."""

from pathlib import Path

import pytest


@pytest.fixture
def project_root() -> Path:
    """프로젝트 루트 디렉토리 경로."""
    return Path(__file__).parent.parent


@pytest.fixture
def fixtures_dir() -> Path:
    """테스트 fixture 파일 디렉토리."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_js_content() -> str:
    """샘플 JavaScript 파일 내용."""
    return """
function processOrder(orderId) {
    const result = fetch('/api/orders/' + orderId);
    document.getElementById('output').innerHTML = result.data;
    return result;
}
""".strip()


@pytest.fixture
def sample_c_content() -> str:
    """샘플 C 파일 내용."""
    return """
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

void process(const char *input) {
    char buffer[256];
    strcpy(buffer, input);
    char *data = malloc(1024);
    if (data == NULL) return;
    sprintf(data, "result: %s", buffer);
    printf("%s\\n", data);
    free(data);
}
""".strip()


@pytest.fixture
def sample_proc_content() -> str:
    """샘플 Pro*C 파일 내용."""
    return """
#include <stdio.h>

EXEC SQL INCLUDE SQLCA;

void update_order(int order_id, const char *status) {
    EXEC SQL BEGIN DECLARE SECTION;
    int h_order_id = order_id;
    char h_status[20];
    EXEC SQL END DECLARE SECTION;

    strcpy(h_status, status);

    EXEC SQL UPDATE ORDERS
        SET STATUS = :h_status
        WHERE ORDER_ID = :h_order_id;

    if (sqlca.sqlcode != 0) {
        EXEC SQL ROLLBACK;
        return;
    }
    EXEC SQL COMMIT;
}
""".strip()


@pytest.fixture
def sample_sql_content() -> str:
    """샘플 SQL 파일 내용."""
    return """
SELECT *
FROM ORDERS o
JOIN CUSTOMERS c ON o.CUSTOMER_ID = c.ID
WHERE YEAR(o.CREATED_AT) = 2026
  AND UPPER(c.NAME) LIKE '%KIM%'
ORDER BY o.CREATED_AT DESC;
""".strip()


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    """임시 출력 디렉토리."""
    out = tmp_path / "output"
    out.mkdir()
    return out

"""models/__init__.py re-export 테스트."""

from mider.models import (
    AnalysisMetadata,
    AnalysisResult,
    CallInfo,
    Checklist,
    ChecklistItem,
    CodeFix,
    DependencyEdge,
    DependencyGraph,
    ExecutionPlan,
    FileContext,
    FileMetadata,
    ImportInfo,
    Issue,
    IssueList,
    IssueListItem,
    IssueSummary,
    Location,
    PatternInfo,
    RiskAssessment,
    SingleFileContext,
    SubTask,
    Summary,
)


def test_all_models_importable():
    """모든 모델이 mider.models에서 import 가능한지 확인."""
    models = [
        FileMetadata,
        SubTask,
        DependencyEdge,
        DependencyGraph,
        ExecutionPlan,
        ImportInfo,
        CallInfo,
        PatternInfo,
        SingleFileContext,
        FileContext,
        Location,
        CodeFix,
        Issue,
        AnalysisResult,
        IssueListItem,
        IssueList,
        ChecklistItem,
        Checklist,
        AnalysisMetadata,
        IssueSummary,
        RiskAssessment,
        Summary,
    ]
    assert len(models) == 22
    for model in models:
        assert hasattr(model, "model_validate"), f"{model.__name__} is not a Pydantic model"

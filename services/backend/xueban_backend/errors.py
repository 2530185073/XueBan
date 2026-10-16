from dataclasses import dataclass
from fastapi import Request
from fastapi.responses import JSONResponse


@dataclass
class Problem(Exception):
    status: int
    category: str
    code: str
    detail: str
    current_revision: int | None = None


def problem_response(request: Request, problem: Problem) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if problem.status == 401 else {}
    return JSONResponse(
        status_code=problem.status,
        media_type="application/problem+json",
        headers=headers,
        content={
            "type": f"https://errors.xueban.invalid/{problem.code}",
            "title": problem.code,
            "status": problem.status,
            "category": problem.category,
            "code": problem.code,
            "detail": problem.detail,
            "request_id": request.state.request_id,
            "current_revision": problem.current_revision,
            "retry_after_seconds": 5 if problem.status == 503 else None,
        },
    )

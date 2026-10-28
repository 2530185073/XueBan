"""Private source receipts survive worker persistence, not public/manual edits."""
from copy import deepcopy

import pytest
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from xueban_backend.correction_models import Entity
from xueban_backend.providers.user_original.source_provenance import issue_source_evidence


@pytest.mark.parametrize("stale", [None, "slots_question_revision", "slots_recognition_revision"])
def test_worker_passes_only_revision_bound_private_source_receipt(flow, stale):
    original_recognize = flow.pipeline.recognize

    def recognize(**kwargs):
        result = original_recognize(**kwargs)
        images = {image.asset_id: image for image in kwargs["images"]}
        for item in result.data["items"]:
            item["source_evidence"] = issue_source_evidence(images, item)
        return result

    flow.pipeline.recognize = recognize
    case = flow.ready()
    with Session(flow.database.engine) as session, session.begin():
        attempt = session.get(Entity, case["current_attempt_id"])
        receipt = deepcopy(attempt.annotations["source_evidence"])
        if stale:
            attempt.annotations = {**attempt.annotations, stale: 999}
    public = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()
    assert "source_evidence" not in public and "_source_evidence" not in public
    observed = {}
    original_check = flow.pipeline.check

    def check(**kwargs):
        observed.update(kwargs)
        return original_check(**kwargs)

    flow.pipeline.check = check
    flow.start_check(case)
    assert flow.worker.run_once()
    assert observed["attempt"].get("_source_evidence") == (receipt if stale is None else None)

"""Run with python3 -m prototype.demo; synthetic data only."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .api import ACTORS, create_app
from .periods import half_month_window


def main():
    k = create_app().state.kernel
    teacher = ACTORS["teacher-east"]
    result = {"notice": "合成数据，确定性规则演示；没有调用真实模型", "students": []}
    for student_id in ("lin", "yu"):
        graph = k.graph(teacher, student_id)
        plan = k.plan(teacher, student_id, "demo-plan", k.students[student_id]["revision"], 15)
        result["students"].append({"student_id": student_id, "agent_id": graph["agent_id"], "knowledge_states": [{"id": n["id"], "state": n["state"], "delayed_verified": n["delayed_verified"]} for n in graph["nodes"]], "plan": plan["tasks"], "preview": k.preview(teacher, student_id)})
    now = datetime.now(timezone.utc)
    current = half_month_window(now.astimezone(ZoneInfo('Asia/Shanghai')).date())
    previous_day = datetime.fromisoformat(current["period_start"]) - timedelta(days=1)
    period = half_month_window(previous_day.astimezone(ZoneInfo('Asia/Shanghai')).date())
    report = k.report(teacher, "lin", "demo-report", k.students["lin"]["revision"], period["period_start"], period["period_end"])
    published = k.publish(teacher, report["id"], "demo-publish", k.students["lin"]["revision"])
    result["published_report"] = {"id": published["id"], "status": published["status"], "period": period, "parent_can_read": k.read_report(ACTORS["parent-lin"], report["id"])["status"] == "published"}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""希望シフトの「提出済み」判定ロジック（希望入力・ダッシュボード・生成チェックリストで共通利用）"""
from utils.constants import EmploymentType


def submitted_employee_ids(employees, requests_list) -> set[int]:
    """希望シフトが「提出済み」とみなせる従業員IDの集合を返す。

    以下のいずれかに該当する従業員は提出済みとみなす:
    - 実際に希望シフトを提出した
    - 社員（出勤前提のため希望提出が不要）
    - 朝食・ディナーともに「おまかせ」のアルバイト（提出不要）
    """
    raw_submitted = {r.employee_id for r in requests_list}
    return {
        e.id for e in employees
        if e.id in raw_submitted
        or e.employment_type == EmploymentType.FULL_TIME
        or (e.always_available_breakfast and e.always_available_dinner)
    }

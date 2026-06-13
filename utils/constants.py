from enum import Enum


class EmploymentType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"

    def label(self):
        return {"full_time": "正社員", "part_time": "アルバイト/パート"}[self.value]



class SkillLevel(str, Enum):
    LEADER = "leader"
    VETERAN = "veteran"
    GENERAL = "general"
    BEGINNER = "beginner"

    def label(self):
        return {"leader": "リーダー", "veteran": "ベテラン", "general": "メンバー", "beginner": "ビギナー"}[self.value]

    def rank(self):
        return {"leader": 3, "veteran": 2, "general": 1, "beginner": 0}[self.value]


class TimeSlot(str, Enum):
    BREAKFAST = "breakfast"
    DINNER = "dinner"

    def label(self):
        return {"breakfast": "朝食(6:00-11:00)", "dinner": "ディナー(17:00-23:00)"}[self.value]

    def short_label(self):
        return {"breakfast": "朝食", "dinner": "ディナー"}[self.value]

    def start_hour(self):
        return {"breakfast": 6, "dinner": 17}[self.value]

    def end_hour(self):
        return {"breakfast": 11, "dinner": 23}[self.value]

    def duration_hours(self):
        return self.end_hour() - self.start_hour()


class Position(str, Enum):
    HALL = "hall"
    KITCHEN = "kitchen"

    def label(self):
        return {"hall": "ホール", "kitchen": "キッチン"}[self.value]


class PrimaryPosition(str, Enum):
    HALL = "hall"
    KITCHEN = "kitchen"

    def label(self):
        return {"hall": "ホール専任", "kitchen": "キッチン専任"}[self.value]

    def short_label(self):
        return {"hall": "ホール", "kitchen": "キッチン"}[self.value]


# シフト制約定数
# min_leader: リーダー（最高習熟度）の最低配置人数
SHIFT_CONSTRAINTS = {
    (TimeSlot.BREAKFAST, Position.HALL):    {"min": 3, "max": 4, "min_leader": 1},
    (TimeSlot.BREAKFAST, Position.KITCHEN): {"min": 3, "max": 3, "min_leader": 1},
    (TimeSlot.DINNER, Position.HALL):       {"min": 2, "max": 3, "min_leader": 1},
    (TimeSlot.DINNER, Position.KITCHEN):    {"min": 3, "max": 3, "min_leader": 2},
}

DAY_OF_WEEK_LABELS = ["月", "火", "水", "木", "金", "土", "日"]

"""Shared count/date contract. Publication dates are interpreted in UTC+8."""
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
import math
import re

BEIJING = timezone(timedelta(hours=8))


def publication_day(timestamp):
    if isinstance(timestamp, bool):return None
    try:
        value=float(timestamp)
        if not math.isfinite(value) or value<=0:return None
        return datetime.fromtimestamp(value,BEIJING).date()
    except (TypeError,ValueError,OverflowError,OSError):return None


def parse_day(value, label):
    if value in (None,''):return None
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ValueError(label+'日期格式应为 YYYY-MM-DD')
    try:return date.fromisoformat(value)
    except ValueError:raise ValueError(label+'日期无效') from None


def pagination_flag(value):
    if isinstance(value,bool):return value
    if type(value) is int and value in (0,1):return bool(value)
    raise ValueError('分页状态缺失或无效，无法确认日期范围已检查完整')


@dataclass(frozen=True)
class CollectionRange:
    limit: int = 0
    date_from: date | None = None
    date_to: date | None = None

    @classmethod
    def parse(cls,limit=None,date_from=None,date_to=None):
        if limit is None or limit=='':limit=0
        if isinstance(limit,bool) or not isinstance(limit,int) or limit<0:
            raise ValueError('采集条数必须是正整数；留空表示不限条数')
        start=parse_day(date_from,'开始');end=parse_day(date_to,'结束')
        if start and end and start>end:raise ValueError('开始日期不能晚于结束日期')
        return cls(limit,start,end)

    @property
    def has_dates(self):return self.date_from is not None or self.date_to is not None

    def matches(self,timestamp):
        if not self.has_dates:return True
        published=publication_day(timestamp)
        return (published is not None and (self.date_from is None or published>=self.date_from)
                and (self.date_to is None or published<=self.date_to))

    def select(self,items):
        selected=[item for item in items if self.matches(item.get('create_time'))]
        if self.has_dates:selected.sort(key=lambda item:float(item['create_time']),reverse=True)
        return selected[:self.limit] if self.limit else selected

    def payload(self):
        return {'limit':self.limit,'date_from':self.date_from.isoformat() if self.date_from else None,
                'date_to':self.date_to.isoformat() if self.date_to else None}

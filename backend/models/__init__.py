# Import all models so they register with Base.metadata before create_all
from models.investors import Investor
from models.ir_users import IRUser
from models.interaction_logs import InteractionLog
from models.outreach_records import OutreachRecord
from models.agent_traces import AgentTrace
from models.agent_feedback import AgentFeedback
from models.calendar_dismissals import CalendarDismissal
from models.ir_investor_membership import IrInvestorMembership
from models.calendar_events import CalendarEventRow

__all__ = [
    "Investor",
    "IRUser",
    "InteractionLog",
    "OutreachRecord",
    "AgentTrace",
    "AgentFeedback",
    "CalendarDismissal",
    "IrInvestorMembership",
    "CalendarEventRow",
]

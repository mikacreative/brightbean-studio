"""Chinese presentation labels and UI-boundary error messages."""

import re

_ZH_UI_LABELS = {
    "Impressions": "展示次数",
    "Reach": "覆盖人数",
    "Views": "观看次数",
    "Plays": "播放次数",
    "Likes": "点赞数",
    "Reactions": "反应数",
    "Comments": "评论数",
    "Replies": "回复数",
    "Shares": "分享数",
    "Reposts": "转发数",
    "Saves": "收藏数",
    "Link clicks": "链接点击数",
    "Outbound clicks": "外链点击数",
    "New follows": "新增关注",
    "Followers": "粉丝数",
    "Subscribers": "订阅者",
    "Watch time": "观看时长",
    "Avg view %": "平均观看百分比",
    "Engagement rate": "互动率",
    "post": "内容",
    "video": "视频",
    "Owner": "所有者",
    "Admin": "管理员",
    "Member": "成员",
    "Manager": "负责人",
    "Editor": "编辑",
    "Contributor": "内容贡献者",
    "Client": "客户",
    "Viewer": "查看者",
    "Draft": "草稿",
    "Pending Review": "待内部审核",
    "Pending Client": "待客户审核",
    "Approved": "已通过",
    "Changes Requested": "待修改",
    "Rejected": "已驳回",
    "Submitted": "已提交",
    "Resubmitted": "已重新提交",
    "Scheduled": "已排期",
    "Publishing": "发布中",
    "Published": "已发布",
    "Partially Published": "部分已发布",
    "Failed": "失败",
    "Hold Requested": "请求暂停",
    "On Hold": "已暂停",
    "Pending": "待处理",
    "Delivered": "已送达",
    "Sent": "已发送",
    "Unread": "未读",
    "Open": "处理中",
    "Resolved": "已处理",
    "Archived": "已归档",
    "Comment": "评论",
    "Mention": "提及",
    "Direct Message": "私信",
    "Review": "评价",
    "Positive": "正面",
    "Neutral": "中性",
    "Negative": "负面",
    "Internal": "内部",
    "External": "对外",
    "Image": "图片",
    "Video": "视频",
    "Document": "文档",
    "Mon": "周一",
    "Tue": "周二",
    "Wed": "周三",
    "Thu": "周四",
    "Fri": "周五",
    "Sat": "周六",
    "Sun": "周日",
    "Monday": "周一",
    "Tuesday": "周二",
    "Wednesday": "周三",
    "Thursday": "周四",
    "Friday": "周五",
    "Saturday": "周六",
    "Sunday": "周日",
    "In-App": "站内通知",
    "Email": "邮件",
    "Post submitted for approval": "内容已提交审批",
    "Post approved": "内容已通过审批",
    "Post changes requested": "内容被要求修改",
    "Post rejected": "内容已驳回",
    "Post published": "内容已发布",
    "Post failed": "内容发布失败",
    "New inbox message": "收到新的互动消息",
    "Inbox SLA overdue": "互动消息处理超时",
    "Client approval requested": "等待客户审批",
    "Team member invited": "已邀请团队成员",
    "Social account disconnected": "社交账号已断开",
    "Report generated": "报告已生成",
    "Engagement alert": "互动提醒",
    "Mentioned in a comment": "在评论中被提及",
    "Approval reminder": "审批提醒",
    "Stalled approval escalation": "审批逾期提醒",
    "Client requested a hold": "客户要求暂停",
    "Client connected accounts": "客户已连接账号",
}


def localize_ui_label(value):
    """Translate built-in labels while preserving unrecognized user content."""
    return _ZH_UI_LABELS.get(str(value), value)


_ERRORS = {
    "Delivery could not be confirmed. Check the conversation before retrying.": "无法确认是否送达，请先检查对话再重试。",
    "the account has hit its rate limit. Wait a few minutes and try again.": "账号已达到请求频率限制，请几分钟后重试。",
    "the connection has expired. Reconnect the account in Workspace Settings.": "账号连接已过期，请在工作区设置中重新连接。",
    "the platform rejected it. Try again, or reconnect the account if this keeps happening.": "平台拒绝了此次发送。请重试；如果问题持续，请重新连接账号。",
    "A comment is required when requesting changes.": "请填写修改意见。",
    "A comment is required when rejecting a post.": "请填写驳回原因。",
    "A comment is required when requesting a hold.": "请填写暂停原因。",
    "A comment is required for bulk rejection.": "请填写批量驳回原因。",
    "Attachment exceeds 5MB limit.": "附件不能超过 5 MB。",
    "Comment not found.": "未找到评论。",
    "Only the comment author can edit.": "只有评论作者可以编辑。",
    "This person is already a member of your organization.": "此人已是组织成员。",
    "An invitation is already pending for this email. You can resend it instead.": "此邮箱已有待接受的邀请，可以重新发送。",
    "Cannot invite someone as an organization owner.": "不能通过邀请指定组织所有者。",
    "Only organization owners can invite someone as an admin.": "只有组织所有者可以邀请管理员。",
    "You cannot grant a workspace role higher than your own in that workspace.": "不能授予高于自己在该工作区中所持有的角色。",
    "This invitation has expired.": "邀请已过期。",
    "This invitation has already been accepted.": "邀请已被接受。",
    "This invitation was sent to a different email address.": "邀请发送到了另一个邮箱地址。",
    "We could not send that invitation right now. Please try again shortly.": "暂时无法发送邀请，请稍后重试。",
    "Cannot revoke an already accepted invitation.": "无法撤销已接受的邀请。",
    "You cannot remove yourself from the organization.": "不能将自己从组织中移除。",
    "Cannot remove the last organization owner.": "不能移除组织中最后一位所有者。",
    "Cannot promote to owner. Transfer ownership instead.": "不能直接将成员提升为所有者，请使用所有权转移。",
    "You cannot change a member whose role is higher than your own.": "不能修改角色高于自己的成员。",
    "Only organization owners can promote someone to admin.": "只有组织所有者可以指定管理员。",
    "Cannot change the role of the last organization owner.": "不能更改组织中最后一位所有者的角色。",
    "You cannot remove a workspace membership whose current role is higher than your own.": "不能移除角色高于自己的工作区成员。",
    "You cannot modify a workspace membership whose current role is higher than your own.": "不能修改角色高于自己的工作区成员。",
    "User does not have client role in this workspace.": "该用户在此工作区中没有客户角色。",
    "We could not send that link right now. Please try again shortly.": "暂时无法发送链接，请稍后重试。",
    "Sign-up is closed on this installation, so only people who already have an account can be invited.": "当前系统已关闭注册，只能邀请已有账号的用户。",
}


def localize_ui_error(error):
    """Localize known UI errors and preserve unknown or provider-supplied details."""
    message = str(error)
    if message in _ERRORS:
        return _ERRORS[message]
    quota = re.fullmatch(
        r"(.+)'s daily API limit is used up, so the account couldn't be connected\."
        r"(?: Try again after (\d{2}:\d{2} UTC)\.)?",
        message,
    )
    if quota:
        retry = f"请在 {quota.group(2)} 后重试。" if quota.group(2) else ""
        return f"{quota.group(1)} 的每日 API 配额已用完，暂时无法连接账号。{retry}"
    throttle = re.fullmatch(
        r"(.+) is temporarily rate-limited, so the account couldn't be connected\."
        r"(?: Try again after (\d{2}:\d{2} UTC)\.)?",
        message,
    )
    if throttle:
        retry = f"请在 {throttle.group(2)} 后重试。" if throttle.group(2) else ""
        return f"{throttle.group(1)} 暂时限制了请求频率，无法连接账号。{retry}"
    match = re.fullmatch(r"This invitation was just sent\. You can send it again in (\d+) minute\(s\)\.", message)
    if match:
        return f"邀请刚刚发送，请在 {match.group(1)} 分钟后重试。"
    return message

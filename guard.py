class Refused(RuntimeError):
    pass


class Guard:
    def __init__(self, channel, list_id, people):
        self.channel = channel
        self.list_id = list_id
        self.people = set(people)

    def reason(self, method, params):
        channel = params.get("channel")
        if method in ("conversations.history", "chat.getPermalink", "reactions.add"):
            return None if channel == self.channel else f"channel {channel} is not this team's channel"
        if method == "chat.postMessage":
            return None if channel in self.people else f"{channel} is not a person on this team"
        if method in ("slackLists.items.list", "slackLists.items.create", "slackLists.items.update"):
            list_id = params.get("list_id")
            return None if list_id == self.list_id else f"list {list_id} is not this team's list"
        return f"{method} is not a method this bot uses"

    def check(self, method, params):
        reason = self.reason(method, params)
        if reason:
            raise Refused(f"guard refused {method}: {reason}")

    def check_file(self, info):
        shares = info.get("shares", {})
        places = (set(info.get("channels", [])) | set(info.get("groups", []))
                  | set(shares.get("private", {})) | set(shares.get("public", {})))
        if self.channel not in places:
            raise Refused(f"guard refused file {info.get('id')}: not shared in this team's channel")

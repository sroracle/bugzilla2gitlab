import base64
import re
import sys

from .utils import _perform_request, format_utc, markdown_table_row

conf = None


def name_or_text(fields, tag):
    tag = fields.find(tag)
    return tag.get("name") or tag.text


class IssueThread(object):
    '''
    Everything related to an issue in GitLab, e.g. the issue itself and subsequent comments.
    '''
    def __init__(self, config, fields):
        global conf
        conf = config
        self.load_objects(fields)

    def load_objects(self, fields):
        '''
        Load the issue object and the comment objects.
        If conf.dry_run=False, then Attachments are created in GitLab in this step.
        '''
        self.issue = Issue(fields)
        self.comments = []
        '''
        fields["long_desc"] gets peared down in Issue creation (above). This is because bugzilla
        lacks the concept of an issue description, so the first comment is harvested for
        the issue description, as well as any subsequent comments that are simply attachments
        from the original reporter. What remains below should be a list of genuine comments.
        '''

        for i, comment in enumerate(fields.findall("long_desc")):
            self.comments.append(Comment(i + 1, fields, comment))

    def save(self):
        '''
        Save the issue and all of the comments to GitLab.
        If conf.dry_run=True, then only the HTTP request that would be made is printed.
        '''
        self.issue.save()

        for comment in self.comments:
            comment.issue_id = self.issue.id
            comment.save()

        # close the issue in GitLab, if it is resolved in Bugzilla
        if self.issue.status in conf.bugzilla_closed_states:
            self.issue.close()


class Issue(object):
    '''
    The issue model
    '''
    required_fields = ["title", "description"]
    data_fields = ["created_at", "title", "description", "assignee_ids", "milestone_id",
                   "labels", "iid"]

    def __init__(self, bugzilla_fields):
        self.headers = conf.default_headers
        self.load_fields(bugzilla_fields)

    def load_fields(self, fields):
        self.iid = fields.findtext("bug_id")
        self.title = fields.findtext("short_desc")
        self.created_at = format_utc(fields.findtext("creation_ts"))
        self.updated_at = format_utc(fields.findtext("delta_ts"))
        self.status = fields.findtext("bug_status")
        self.create_labels(fields.findtext("keywords"))
        milestone = fields.findtext("target_milestone")
        if conf.map_milestones and milestone not in conf.milestones_to_skip:
            self.create_milestone(milestone)
        self.create_description(fields)

    def create_labels(self, keywords):
        labels = []
        if conf.default_gitlab_labels:
            labels.extend(conf.default_gitlab_labels)

        if conf.map_keywords and keywords:
            # Input: payload of XML element like this: <keywords>SECURITY, SUPPORT</keywords>
            # Bugzilla restriction: You may not use commas or whitespace in a keyword name.
            for k in keywords.replace(" ", "").split(","):
                if not (conf.keywords_to_skip and k in conf.keywords_to_skip):
                    labels.append(k)

        self.labels = ",".join(labels)

    def create_milestone(self, milestone):
        '''
        Looks up milestone id given its title or creates a new one.
        '''
        if milestone not in conf.gitlab_milestones:
            url = "{}/projects/{}/milestones".format(conf.gitlab_base_url, conf.gitlab_project_id)
            response = _perform_request(
                url, "post", headers=self.headers, data={"title": milestone})
            conf.gitlab_milestones[milestone] = response["id"]

        self.milestone_id = conf.gitlab_milestones[milestone]

    def create_description(self, fields):
        '''
        An opinionated description body creator.
        '''
        ext_description = ""

        # markdown table header
        self.description = markdown_table_row("", "")
        self.description += markdown_table_row("---", "---")

        self.description += markdown_table_row("Bugzilla ID", self.iid)
        aliases = [i.text for i in fields.findall("alias")]
        if any(aliases):
            self.description += markdown_table_row("Alias(es)", ", ".join(aliases))

        reporter = name_or_text(fields, "reporter")
        self.description += markdown_table_row("Reporter", reporter)
        assignee = name_or_text(fields, "assigned_to")
        if assignee:
            self.description += markdown_table_row("Assignee", assignee)
        self.description += markdown_table_row("Reported", fields.findtext("creation_ts"))
        self.description += markdown_table_row("Modified", fields.findtext("delta_ts"))
        self.description += markdown_table_row(
            "Status",
            fields.findtext("bug_status") + " " + fields.findtext("resolution"),
        )

        self.description += markdown_table_row("Version", fields.findtext("version"))
        self.description += markdown_table_row(
            "Hardware",
            fields.findtext("op_sys") + " / " + fields.findtext("rep_platform"),
        )
        self.description += markdown_table_row(
            "Importance",
            fields.findtext("priority") + " / " + fields.findtext("bug_severity"),
        )
        url = fields.findtext("bug_file_loc")
        if url:
            self.description += markdown_table_row("URL", url)
        see_also = [i.text for i in fields.findall("see_also")]
        if any(see_also):
            self.description += markdown_table_row("See also", "<br>".join(see_also))

        comment0 = fields.find("long_desc")
        if (reporter == name_or_text(comment0, "who") and comment0.findtext("thetext")):
            ext_description += "\n## Description\n\n"
            text = comment0.findtext("thetext").split("\n")
            attachid = comment0.findtext("attachid")
            if text and text[0].startswith("Created attachment") and attachid:
                text[0] = Attachment.from_bug(fields, attachid)
            ext_description += "  \n".join(text)
            fields.remove(comment0)

        if ext_description:
            self.description += ext_description

    def validate(self):
        for field in self.required_fields:
            value = getattr(self, field)
            if not value:
                raise Exception("Missing value for required field: {}".format(field))
        return True

    def save(self):
        self.validate()
        url = "{}/projects/{}/issues".format(conf.gitlab_base_url, conf.gitlab_project_id)
        data = {k: v for k, v in self.__dict__.items() if k in self.data_fields}

        response = _perform_request(url, "post", headers=self.headers, data=data, json=True,
                                    dry_run=conf.dry_run)

        if conf.dry_run:
            # assign a random number so that program can continue
            self.id = 5
            return

        self.id = response["iid"]

    def close(self):
        url = "{}/projects/{}/issues/{}".format(conf.gitlab_base_url, conf.gitlab_project_id,
                                                self.id)
        data = {
            "state_event": "close",
            "updated_at": self.updated_at,
        }

        _perform_request(url, "put", headers=self.headers, data=data, dry_run=conf.dry_run)


class Comment(object):
    '''
    The comment model
    '''

    required_fields = ["body", "issue_id"]
    data_fields = ["created_at", "body"]

    def __init__(self, num, bug, comment):
        self.num = num
        self.headers = conf.default_headers
        self.load_fields(bug, comment)

    def load_fields(self, bug, fields):
        self.created_at = format_utc(fields.findtext("bug_when"))
        who = name_or_text(fields, "who")
        when = fields.findtext("bug_when")
        self.body = f"**Comment {self.num} by \"{who}\" on {when}**\n\n"

        text = fields.findtext("thetext").split("\n")
        attachid = fields.findtext("attachid")
        if text and text[0].startswith("Created attachment") and attachid:
            text[0] = Attachment.from_bug(bug, attachid)
        self.body += "  \n".join(text)

    def validate(self):
        for field in self.required_fields:
            value = getattr(self, field)
            if not value:
                raise Exception("Missing value for required field: {}".format(field))

    def save(self):
        self.validate()
        url = "{}/projects/{}/issues/{}/notes".format(conf.gitlab_base_url, conf.gitlab_project_id,
                                                      self.issue_id)
        data = {k: v for k, v in self.__dict__.items() if k in self.data_fields}

        _perform_request(url, "post", headers=self.headers, data=data, json=True,
                         dry_run=conf.dry_run)


class Attachment(object):
    '''
    The attachment model
    '''
    def __init__(self, attachment):
        self.id = attachment.findtext("attachid")
        self.filename = attachment.findtext("filename")
        self.obsolete = attachment.get("isobsolete") == "1"
        data = attachment.find("data")
        encoding = data.get("encoding")
        if encoding != "base64":
            raise ValueError(encoding + " encoding is not supported")
        self.content = base64.standard_b64decode(data.text)
        self.headers = conf.default_headers

    def save(self):
        url = "{}/projects/{}/uploads".format(conf.gitlab_base_url, conf.gitlab_project_id)
        f = {"file": (self.filename, self.content)}
        attachment = _perform_request(url, "post", headers=self.headers, files=f, json=True,
                                      dry_run=conf.dry_run)
        # For dry run, nothing is uploaded, so upload link is faked just to let the process continue
        return "" if conf.dry_run else attachment["url"]

    @classmethod
    def from_bug(cls, bug, attachid):
        attachment = cls(bug.find(f"attachment[attachid='{attachid}']"))
        attachlink = attachment.save()
        if attachment.obsolete:
            return f"**Created ~~[attachment {attachid}]({attachlink})~~**"
        return f"**Created [attachment {attachid}]({attachlink})**"

import re

from .utils import _perform_request, format_utc, markdown_table_row

conf = None


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

        for comment_fields in fields["long_desc"]:
            if comment_fields.get("thetext"):
                self.comments.append(Comment(comment_fields))

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
                   "labels"]

    def __init__(self, bugzilla_fields):
        self.headers = conf.default_headers
        self.load_fields(bugzilla_fields)

    def load_fields(self, fields):
        self.title = fields["short_desc"]
        self.created_at = format_utc(fields["creation_ts"])
        self.status = fields["bug_status"]
        self.create_labels(fields["component"], fields.get("op_sys"), fields.get("keywords"))
        milestone = fields["target_milestone"]
        if conf.map_milestones and milestone not in conf.milestones_to_skip:
            self.create_milestone(milestone)
        self.create_description(fields)

    def create_labels(self, component, operating_system, keywords):
        '''
        Creates 4 types of labels: default labels listed in the configuration, component labels,
        operating system labels, and keyword labels.
        '''
        labels = []
        if conf.default_gitlab_labels:
            labels.extend(conf.default_gitlab_labels)

        component_label = conf.component_mappings.get(component)
        if component_label:
            labels.append(component_label)

        # Do not create a label if the OS is other. That is a meaningless label.
        if conf.map_operating_system and operating_system and operating_system != "Other":
            labels.append(operating_system)

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

        if conf.include_bugzilla_link:
            bug_id = fields["bug_id"]
            link = "{}/show_bug.cgi?id={}".format(conf.bugzilla_base_url, bug_id)
            self.description += markdown_table_row("Bugzilla Link",
                                                   "[{}]({})".format(bug_id, link))

        self.description += markdown_table_row("Created on", fields["creation_ts"])

        if fields.get("resolution"):
            self.description += markdown_table_row("Resolution", fields["resolution"])
            self.description += markdown_table_row("Resolved on", fields["delta_ts"])

        self.description += markdown_table_row("Version", fields.get("version"))
        self.description += markdown_table_row("OS", fields.get("op_sys"))
        self.description += markdown_table_row("Architecture", fields.get("rep_platform"))

        # add first comment to the issue description
        attachments = []
        to_delete = []
        comment0 = fields["long_desc"][0]
        if (fields["reporter"] == comment0["who"] and comment0["thetext"]):
            ext_description += "\n## Extended Description \n"
            ext_description += "\n\n".join(re.split("\n+", comment0["thetext"]))
            self.update_attachments(fields["reporter"], comment0, attachments)
            del fields["long_desc"][0]

        for i in range(0, len(fields["long_desc"])):
            comment = fields["long_desc"][i]
            if self.update_attachments(fields["reporter"], comment, attachments):
                to_delete.append(i)

        # delete comments that have already added to the issue description
        for i in reversed(to_delete):
            del fields["long_desc"][i]

        if attachments:
            self.description += markdown_table_row("Attachments", ", ".join(attachments))

        self.description += markdown_table_row("Reporter", fields["reporter"])

        if ext_description:
            self.description += ext_description

    def update_attachments(self, reporter, comment, attachments):
        '''
        Fetches attachments from comment if authored by reporter.
        '''
        if comment.get("attachid") and comment.get("who") == reporter:
            filename = Attachment.parse_file_description(comment.get("thetext"))
            attachment_markdown = Attachment(comment.get("attachid"), filename).save()
            attachments.append(attachment_markdown)
            return True
        return False

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
        }

        _perform_request(url, "put", headers=self.headers, data=data, dry_run=conf.dry_run)


class Comment(object):
    '''
    The comment model
    '''

    required_fields = ["body", "issue_id"]
    data_fields = ["created_at", "body"]

    def __init__(self, bugzilla_fields):
        self.headers = conf.default_headers
        self.load_fields(bugzilla_fields)

    def load_fields(self, fields):
        self.created_at = format_utc(fields["bug_when"])
        self.body = "By {} on {}\n\n".format(fields["who"], fields["bug_when"])

        # if this comment is actually an attachment, upload the attachment and add the
        # markdown to the comment body
        if fields.get("attachid"):
            filename = Attachment.parse_file_description(fields["thetext"])
            attachment_markdown = Attachment(fields["attachid"], filename).save()
            self.body += attachment_markdown
        else:
            self.body += fields["thetext"]

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
    def __init__(self, bugzilla_attachment_id, file_description):
        self.id = bugzilla_attachment_id
        self.file_description = file_description
        self.headers = conf.default_headers

    @classmethod
    def parse_file_description(cls, comment):
        regex = r"^Created attachment (\d*)\s?(.*)$"
        matches = re.match(regex, comment, flags=re.M)
        if not matches:
            raise Exception("Failed to match comment string: {}".format(comment))
        return matches.group(2)

    def parse_file_name(self, headers):
        # Use real filename to store attachment but descriptive name for issue text
        if 'Content-disposition' not in headers:
            raise Exception(u"No file name returned for attachment {}"
                            .format(self.file_description))
        # Content-disposition: application/zip; filename="mail_route.zip"
        regex = r"^.*; filename=\"(.*)\"$"
        matches = re.match(regex, headers['Content-disposition'], flags=re.M)
        if not matches:
            raise Exception("Failed to match file name for string: {}"
                            .format(headers['Content-disposition']))
        return matches.group(1)

    def parse_upload_link(self, attachment):
        if not (attachment and attachment["markdown"]):
            raise Exception(u"No markdown returned for upload of attachment {}"
                            .format(self.file_description))
        # ![mail_route.zip](/uploads/e943e69eb2478529f2f1c7c7ea00fb46/mail_route.zip)
        regex = r"^!?\[.*\]\((.*)\)$"
        matches = re.match(regex, attachment["markdown"], flags=re.M)
        if not matches:
            raise Exception("Failed to match upload link for string: {}"
                            .format(attachment["markdown"]))
        return matches.group(1)

    def save(self):
        url = "{}/attachment.cgi?id={}".format(conf.bugzilla_base_url, self.id)
        result = _perform_request(url, "get", json=False)
        filename = self.parse_file_name(result.headers)

        url = "{}/projects/{}/uploads".format(conf.gitlab_base_url, conf.gitlab_project_id)
        f = {"file": (filename, result.content)}
        attachment = _perform_request(url, "post", headers=self.headers, files=f, json=True,
                                      dry_run=conf.dry_run)
        # For dry run, nothing is uploaded, so upload link is faked just to let the process continue
        upload_link = self.file_description if conf.dry_run else self.parse_upload_link(attachment)

        return u"[{}]({})".format(self.file_description, upload_link)


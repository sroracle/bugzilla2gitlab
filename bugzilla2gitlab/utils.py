import sys
from getpass import getpass

import dateutil.parser
from defusedxml import ElementTree
import pytz
import requests

session = None


def _perform_request(url, method, data={}, params={}, headers={}, files={}, json=True,
                     dry_run=False):
    '''
    Utility method to perform an HTTP request.
    '''
    if dry_run and method != "get":
        msg = "{} {} dry_run".format(url, method)
        print(msg, file=sys.stderr)
        return 0

    global session
    if not session:
        session = requests.Session()

    func = getattr(session, method)

    if files:
        result = func(url, files=files, headers=headers)
    else:
        result = func(url, params=params, data=data, headers=headers)

    if result.status_code in [200, 201]:
        if json:
            return result.json()
        else:
            return result

    raise Exception("{} failed requests: {}".format(result.status_code, result.reason))


def markdown_table_row(key, value):
    '''
    Create a row in a markdown table.
    '''
    return u"| {} | {} |\n".format(key, value)


def format_utc(datestr):
    '''
    Convert dateime string to UTC format recognized by gitlab.
    '''
    parsed_dt = dateutil.parser.parse(datestr)
    utc_dt = parsed_dt.astimezone(pytz.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_bugzilla_bug(bugzilla_url, bug_id):
    bug_xml = _fetch_bug_content(bugzilla_url, bug_id)
    tree = ElementTree.fromstring(bug_xml)
    return tree.find("bug")


def _fetch_bug_content(url, bug_id):
    url = "{}/show_bug.cgi?ctype=xml&id={}".format(url, bug_id)
    response = _perform_request(url, "get", json=False)
    return response.content


def bugzilla_login(url, user):
    '''
    Log in to Bugzilla as user, asking for password for a few times / untill success.
    '''
    max_login_attempts = 3
    login_url = "{}/index.cgi".format(url)
    # CSRF protection bypass: GET, then POST
    _perform_request(login_url, "get", json=False)
    for attempt in range(max_login_attempts):
        response = _perform_request(
            login_url,
            "post",
            headers={'Referer': login_url},
            data={
                'Bugzilla_login': user,
                'Bugzilla_password': getpass("Bugzilla password for {}: ".format(user))},
            json=False)
        if response.cookies:
            break
        else:
            print("Failed to log in (attempt {})".format(attempt + 1), file=sys.stderr)
    else:
        raise Exception("Failed to log in after {} attempts".format(max_login_attempts))


def validate_list(integer_list):
    '''
    Ensure that the user-supplied input is a list of integers, or a list of strings
    that can be parsed as integers.
    '''
    if not integer_list:
        raise Exception("No bugs to migrate! Call `migrate` with a list of bug ids.")

    if not isinstance(integer_list, list):
        raise Exception("Expected a list of integers. Instead recieved "
                        "a(n) {}".format(type(integer_list)))

        for i in integer_list:
            try:
                int(i)
            except ValueError:
                raise Exception("{} is not able to be parsed as an integer, "
                                "and is therefore an invalid bug id.".format(i))

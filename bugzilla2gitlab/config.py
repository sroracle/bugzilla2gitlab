from collections import namedtuple
import os
import sys

import yaml

from .utils import _perform_request

Config = namedtuple('Config', ["gitlab_base_url", "gitlab_project_id",
                               "bugzilla_base_url", "bugzilla_user",
                               "bugzilla_closed_states", "default_headers",
                               "default_gitlab_labels",
                               "map_keywords", "keywords_to_skip",
                               "map_milestones", "milestones_to_skip", "gitlab_milestones",
                               "dry_run", "include_bugzilla_link"])


def get_config(path):
    configuration = {}
    configuration.update(_load_defaults(path))
    if configuration["map_milestones"]:
        configuration.update(
            _load_milestone_id_cache(configuration["gitlab_project_id"],
                                     configuration["gitlab_base_url"],
                                     configuration["default_headers"]))
    return Config(**configuration)


def _load_defaults(path):
    with open(os.path.join(path, "defaults.yml")) as f:
        config = yaml.safe_load(f)

    defaults = {}

    for key in config:
        if key == "gitlab_private_token":
            defaults["default_headers"] = {"private-token": config[key]}
        else:
            defaults[key] = config[key]

    return defaults


def _load_milestone_id_cache(project_id, gitlab_url, gitlab_headers):
    '''
    Load cache of GitLab milestones and ids
    '''
    print("Loading milestone cache...", file=sys.stderr)

    gitlab_milestones = {}
    url = "{}/projects/{}/milestones".format(gitlab_url, project_id)
    result = _perform_request(url, "get", headers=gitlab_headers)
    if result and isinstance(result, list):
        for milestone in result:
            gitlab_milestones[milestone["title"]] = milestone["id"]

    return {"gitlab_milestones": gitlab_milestones}


def _get_user_id(username, gitlab_url, headers):
    url = "{}/users?username={}".format(gitlab_url, username)
    result = _perform_request(url, "get", headers=headers)
    if result and isinstance(result, list):
        return result[0]["id"]
    else:
        raise Exception("No gitlab account found for user {}".format(username))

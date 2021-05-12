1. Set `gitlab_private_token` in `config/defaults.yml`
2. `python3 -m venv ./venv`
3. `. ./venv/bin/activate`
4. `pip install -r requirements.txt`
5. For each product / component in BTS:
   1. Change `gitlab_project_id` in `config/defaults.yml`
   2. Change line 193 of `bugzilla2gitlab/models.py` to contain the
      appropriate Gitlab URL
   3. `bin/bugzilla2gitlab config/bugs_PROJECT.txt config | tee -a config/map.csv`

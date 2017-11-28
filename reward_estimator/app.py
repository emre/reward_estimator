import re

from steem import Steem
from steem.amount import Amount
from steem.post import Post
from dateutil.parser import parse
from steembase.exceptions import PostDoesNotExist

from flask import Flask, render_template, abort, request, redirect
import redis

app = Flask(__name__)

s = Steem(nodes=["https://rpc.buildteam.io"])
r = redis.Redis()


def get_reward_fund(steemd):
    reward_balance = r.get("reward_balance")
    recent_claims = r.get("recent_claims")
    if not reward_balance or recent_claims:
        reward_fund = steemd.get_reward_fund('post')
        r.set("reward_balance", reward_fund["reward_balance"])
        r.expire("reward_balance", 150)
        r.set("recent_claims", reward_fund["recent_claims"])
        r.expire("recent_claims", 150)
        reward_balance = reward_fund["reward_balance"]
        recent_claims = reward_fund["recent_claims"]

    if isinstance(reward_balance, bytes):
        reward_balance = reward_balance.decode("utf-8")

    if isinstance(recent_claims, bytes):
        recent_claims = recent_claims.decode("utf-8")

    return reward_balance, recent_claims


def get_base_price(steemd):
    key = "base_price"
    base_price = r.get(key)
    if not base_price:
        base_price = steemd.get_current_median_history_price()["base"]
        r.set(key, base_price)
        r.expire(key, 150)
    if isinstance(base_price, bytes):
        base_price = base_price.decode("utf-8")

    return base_price


def curation_reward_pct(post_created_at, vote_created_at):
    reward = ((vote_created_at - post_created_at).seconds / 1800) * 100
    if reward > 100:
        reward = 100
    return reward


def get_payout_from_rshares(reward_balance, recent_claims, base_price, rshares):
    fund_per_share = Amount(reward_balance).amount / float(recent_claims)
    payout = rshares * fund_per_share * Amount(base_price).amount

    return payout


def calculate_rewards(steemd, post):

    total_post_rewards = 0
    total_curation_rewards = 0

    reward_balance, recent_claims = get_reward_fund(steemd)
    base_price = get_base_price(steemd)

    for vote in post["active_votes"]:

        curation_reward_percent = curation_reward_pct(
            post["created"], parse(vote["time"]))

        vote_payout = get_payout_from_rshares(
            reward_balance,
            recent_claims,
            base_price,
            float(vote["rshares"]))

        curation_payout = get_payout_from_rshares(
            reward_balance,
            recent_claims,
            base_price,
            float(vote["rshares"]) * curation_reward_percent / 400)

        total_post_rewards += vote_payout
        total_curation_rewards += curation_payout

    total_author_rewards = total_post_rewards - total_curation_rewards
    if post.get("beneficiaries"):
        beneficiaries_sum = sum(
            b["weight"] for b in post["beneficiaries"]) / 100
        total_author_rewards = total_author_rewards * (
            100 - beneficiaries_sum) / 100

    total = round(total_post_rewards, 2)
    curation = round(total_curation_rewards, 2)
    author = round(total_author_rewards, 2)
    beneficiaries = round((total - curation - author), 2)

    return total, curation, author, beneficiaries


@app.route('/')
def index():
    if request.query_string and request.args.get('url'):
        url = request.args.get('url')
        url = url.replace("https://", "")
        url = url.replace("http://", "")
        url = re.sub("^(.*?)/", "", url)

        return redirect('/' + url)
    return render_template("index.html")


@app.route('/<_>/@<username>/<permlink>')
def profile(_, username, permlink):
    try:
        post = Post("@%s/%s" % (username, permlink))
    except PostDoesNotExist:
        abort(404)
        
    total, curation, author, beneficiaries = calculate_rewards(s, post)

    return render_template(
        "rewards.html",
        post=post,
        total=total,
        curation=curation,
        author=author,
        beneficiaries=beneficiaries,
    )
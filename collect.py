"""Collect Worlds pros' NA solo queue games from the Riot API and compute champion/matchup stats.

Usage: python collect.py [--team TL] [--days 4] [--all-platforms]
Incremental: resolved PUUIDs and match details are cached under cache/, so re-runs only fetch new games.
"""
import argparse, csv, json, os, sys, time, urllib.parse, urllib.request, urllib.error
from collections import defaultdict, deque

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache")
MATCH_DIR = os.path.join(CACHE, "matches")
ROUTING = {"NA1": "americas", "BR1": "americas", "KR": "asia"}
PLATFORM_HOST = {"NA1": "na1", "BR1": "br1", "KR": "kr"}


def load_key():
    env = os.path.join(ROOT, ".env")
    for line in open(env, encoding="utf-8"):
        if line.startswith("RIOT_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("RIOT_API_KEY missing from .env")


class Riot:
    """Minimal client: stays under 18 req/s and 95 req/2min, honours Retry-After on 429."""

    def __init__(self, key):
        self.key = key
        self.recent = deque()

    def _wait(self):
        while True:
            now = time.time()
            while self.recent and now - self.recent[0] > 120:
                self.recent.popleft()
            last_sec = sum(1 for t in self.recent if now - t < 1)
            if len(self.recent) < 95 and last_sec < 18:
                self.recent.append(now)
                return
            time.sleep(0.25)

    def get(self, url):
        for attempt in range(5):
            self._wait()
            req = urllib.request.Request(url, headers={"X-Riot-Token": self.key, "User-Agent": "worlds-scout/0.1 (personal project)"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(int(e.headers.get("Retry-After", "5")) + 1)
                elif e.code == 404:
                    return None
                elif e.code in (401, 403):
                    sys.exit(f"Riot API refused {e.code} for {url.split('?')[0]}: {e.read()[:200]!r}")
                elif e.code >= 500:
                    time.sleep(2 * (attempt + 1))
                else:
                    raise
        return None


def read_json(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


TL_DIR = os.path.join(CACHE, "timelines")


def fetch_timeline(riot, host, mid):
    """Cache only each participant's total gold at minute 15 (null if the game ended earlier)."""
    path = os.path.join(TL_DIR, f"{mid}.json")
    if os.path.exists(path) or not os.path.exists(os.path.join(MATCH_DIR, f"{mid}.json")):
        return
    t = riot.get(f"https://{host}.api.riotgames.com/lol/match/v5/matches/{mid}/timeline")
    if not t:
        return
    frames = t["info"]["frames"]
    gold = {pid: f["totalGold"] for pid, f in frames[15]["participantFrames"].items()} if len(frames) > 15 else {}
    os.makedirs(TL_DIR, exist_ok=True)
    json.dump(gold, open(path, "w", encoding="utf-8"))


def save(players, days):
    stats = compute(players)
    stats["days"] = days
    json.dump(stats, open(os.path.join(ROOT, "data", "stats.json"), "w", encoding="utf-8"), ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team")
    ap.add_argument("--days", type=float, default=4)
    ap.add_argument("--all-platforms", action="store_true")
    args = ap.parse_args()

    os.makedirs(MATCH_DIR, exist_ok=True)
    riot = Riot(load_key())

    rows = list(csv.DictReader(open(os.path.join(ROOT, "data", "accounts.csv"), encoding="utf-8"), delimiter="|"))
    if not args.all_platforms:
        rows = [r for r in rows if r["platform"] == "NA1"]
    if args.team:
        rows = [r for r in rows if r["team"].lower() == args.team.lower()]

    puuids = read_json(os.path.join(CACHE, "puuids.json"), {})
    since = int(time.time() - args.days * 86400)

    players = []
    for i, row in enumerate(rows, 1):
        rid = row["riotId"]
        name, _, tag = rid.rpartition("#")
        host = ROUTING[row["platform"]]
        if rid not in puuids:
            acct = riot.get(f"https://{host}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/"
                            f"{urllib.parse.quote(name)}/{urllib.parse.quote(tag)}")
            puuids[rid] = acct["puuid"] if acct else None
            json.dump(puuids, open(os.path.join(CACHE, "puuids.json"), "w", encoding="utf-8"))
        puuid = puuids[rid]
        if not puuid:
            print(f"[{i}/{len(rows)}] {row['team']} {row['handle']}: {rid} not found (renamed?)")
            players.append({**row, "puuid": None, "match_ids": []})
            continue
        ids = riot.get(f"https://{host}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
                       f"?queue=420&type=ranked&startTime={since}&count=100") or []
        for mid in ids:
            path = os.path.join(MATCH_DIR, f"{mid}.json")
            if not os.path.exists(path):
                m = riot.get(f"https://{host}.api.riotgames.com/lol/match/v5/matches/{mid}")
                if m:
                    json.dump(m, open(path, "w", encoding="utf-8"))
            fetch_timeline(riot, host, mid)
        print(f"[{i}/{len(rows)}] {row['team']} {row['handle']}: {len(ids)} games", flush=True)
        players.append({**row, "puuid": puuid, "match_ids": ids})
        if i % 10 == 0:
            save(players, args.days)

    save(players, args.days)
    print("wrote data/stats.json")


PARTNER_POS = {"JUNGLE": "MIDDLE", "MIDDLE": "JUNGLE", "BOTTOM": "UTILITY", "UTILITY": "BOTTOM"}


def top_n(counter, n):
    return [{"id": k, "n": v} for k, v in sorted(counter.items(), key=lambda kv: -kv[1])[:n]]


def compute(players):
    out = []
    for p in players:
        champs = defaultdict(lambda: {"games": 0, "wins": 0, "k": 0, "d": 0, "a": 0, "cs": 0, "secs": 0,
                                      "gd": [], "keystones": defaultdict(int), "trees": defaultdict(int),
                                      "vs": defaultdict(lambda: [0, 0]), "with": defaultdict(lambda: [0, 0]),
                                      "log": []})
        last = 0
        for mid in p["match_ids"]:
            m = read_json(os.path.join(MATCH_DIR, f"{mid}.json"), None)
            if not m:
                continue
            info = m["info"]
            parts = info["participants"]
            me = next((x for x in parts if x["puuid"] == p["puuid"]), None)
            if not me:
                continue
            gold = read_json(os.path.join(TL_DIR, f"{mid}.json"), {})
            pos = me["teamPosition"]
            foe = next((x for x in parts if x["teamId"] != me["teamId"] and pos and x["teamPosition"] == pos), None)
            mate = next((x for x in parts if x["teamId"] == me["teamId"] and x is not me
                         and PARTNER_POS.get(pos) and x["teamPosition"] == PARTNER_POS[pos]), None)
            gd = None
            if foe and str(me["participantId"]) in gold and str(foe["participantId"]) in gold:
                gd = gold[str(me["participantId"])] - gold[str(foe["participantId"])]
            c = champs[me["championName"]]
            c["games"] += 1
            c["wins"] += int(me["win"])
            c["k"] += me["kills"]; c["d"] += me["deaths"]; c["a"] += me["assists"]
            c["cs"] += me["totalMinionsKilled"] + me["neutralMinionsKilled"]
            c["secs"] += info["gameDuration"]
            if gd is not None:
                c["gd"].append(gd)
            styles = me.get("perks", {}).get("styles", [])
            if len(styles) >= 2 and styles[0]["selections"]:
                c["keystones"][styles[0]["selections"][0]["perk"]] += 1
                c["trees"][styles[1]["style"]] += 1
            if foe:
                v = c["vs"][foe["championName"]]
                v[0] += 1; v[1] += int(me["win"])
            if mate:
                w = c["with"][mate["championName"]]
                w[0] += 1; w[1] += int(me["win"])
            c["log"].append({"t": info["gameCreation"], "win": bool(me["win"]),
                             "enemy": foe["championName"] if foe else None,
                             "partner": mate["championName"] if mate else None, "gd": gd})
            last = max(last, info["gameCreation"])
        champions = []
        for name, c in champs.items():
            champions.append({
                "champion": name, "games": c["games"], "wins": c["wins"],
                "kda": round((c["k"] + c["a"]) / max(c["d"], 1), 2),
                "kills": round(c["k"] / c["games"], 1), "deaths": round(c["d"] / c["games"], 1),
                "assists": round(c["a"] / c["games"], 1),
                "csPerMin": round(c["cs"] / (c["secs"] / 60), 1),
                "gd15": round(sum(c["gd"]) / len(c["gd"])) if c["gd"] else None,
                "keystones": top_n(c["keystones"], 2), "trees": top_n(c["trees"], 1),
                "matchups": sorted(({"champ": e, "games": g, "wins": w} for e, (g, w) in c["vs"].items()),
                                   key=lambda x: -x["games"]),
                "partners": sorted(({"champ": e, "games": g, "wins": w} for e, (g, w) in c["with"].items()),
                                   key=lambda x: -x["games"]),
                "log": sorted(c["log"], key=lambda x: -x["t"])})
        champions.sort(key=lambda x: -x["games"])
        games = sum(c["games"] for c in champions)
        out.append({"team": p["team"], "handle": p["handle"], "role": p["role"], "riotId": p["riotId"],
                    "found": bool(p["puuid"]), "games": games, "wins": sum(c["wins"] for c in champions),
                    "lastGame": last, "champions": champions})
    return {"generatedAt": int(time.time() * 1000), "players": out}


if __name__ == "__main__":
    main()

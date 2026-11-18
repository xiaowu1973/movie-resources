# coding=utf-8
"""
X影视 · TVBox Python 爬虫
================================================================
站点 : https://xmovie.suyingok.com
数据 : https://xmov.xsocdn.com

为什么要走接口而不是抓网页
----------------------------------------------------------------
该站是 hash 路由单页应用（#/category/movie、#/film/513982、#/watch/513982），
服务端对任何路径都只回同一个 13962 字节的空壳 HTML（/category/movie、
/film/513982、/movie-xxx 全部 404），片单由 app.js 在浏览器端拉 JSON 渲染；
播放页的 <video src="blob:..."> 是 hls.js 的 MSE 对象，源码里没有任何 m3u8。
所以 csp_XBPQ 那套"抓 HTML + 正则截取"必然空转，只能直接读它的静态数据接口。

接口格式（实测 2026-10-29）
----------------------------------------------------------------
meta    : /data/1/meta.json
首页    : /data/1/home.json                      sections[].items[]
分类    : /data/1/list/{cat}/{sort}/{page}/{genre}/{region}/{year}/{form}.json
搜索    : /data/1/search/{cat}/{sort}/{page}/{keyword}/{genre}/{region}/{year}/{form}.json
详情    : /data/1/work/{id}.json                  lines[] 里带 playlist / playlist_v3
剧集(v2): playlist  -> items[].url               直接就是 m3u8
剧集(v3): playlist_v3 -> items[].suffix + origins[origin_id]   拼出 m3u8
空参数占位符是 "~"；非空参数按 encodeURIComponent 编码后再加前缀 "v"。
返回体：items[] / total / pages / page / page_size

TVBox 接入
----------------------------------------------------------------
    {"key":"X影视","name":"X影视","type":3,"api":"./py/X影视.py",
     "searchable":1,"quickSearch":1,"filterable":1,"ext":""}

本地自测（PC 上装好 requests 直接跑）
----------------------------------------------------------------
    python X影视.py
"""

import json
import re
from urllib.parse import quote

try:
    import requests as _requests
except Exception:                                   # 没有 requests 时退回 urllib
    _requests = None


class Spider(object):

    SITE = "https://xmovie.suyingok.com"
    API = "https://xmov.xsocdn.com"
    VER = "1"
    UA = ("Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")
    TIMEOUT = 12
    LIMIT = 24
    # 这几个分类站方默认按"热门"排，其余默认"最新"
    HOT_SLUGS = ("movie", "tv", "variety", "animation", "documentary")

    def __init__(self):
        self.name = "X影视"
        self.header = {
            "User-Agent": self.UA,
            "Referer": self.SITE + "/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        self._sess = _requests.Session() if _requests else None
        self._meta = None
        self._home = None
        self._work_cache = {}

    # ------------------------------------------------------------------
    # TVBox 固定接口
    # ------------------------------------------------------------------
    def getName(self):
        return self.name

    def getDependence(self):
        return []

    def manualVideoCheck(self):
        return False

    def action(self, action):
        return {}

    def destroy(self):
        pass

    def isVideoFormat(self, url):
        if not url:
            return False
        u = str(url).lower()
        if ".m3u8" in u:
            return True
        return u.endswith((".mp4", ".flv", ".mkv", ".ts", ".avi"))

    def init(self, extend=""):
        cfg = {}
        if isinstance(extend, dict):
            cfg = extend
        elif isinstance(extend, str) and extend.strip().startswith("{"):
            try:
                c = json.loads(extend)
                if isinstance(c, dict):
                    cfg = c
            except Exception:
                cfg = {}
        if cfg.get("site"):
            self.SITE = str(cfg["site"]).rstrip("/")
            self.header["Referer"] = self.SITE + "/"
        if cfg.get("api"):
            self.API = str(cfg["api"]).rstrip("/")
        if cfg.get("ua"):
            self.header["User-Agent"] = str(cfg["ua"])
        if cfg.get("timeout"):
            try:
                self.TIMEOUT = int(cfg["timeout"])
            except Exception:
                pass
        self._meta = None
        self._home = None
        self._work_cache = {}

    # ------------------------------------------------------------------
    # 基础请求
    # ------------------------------------------------------------------
    def _get(self, url):
        if not url:
            return ""
        if self._sess is not None:
            try:
                r = self._sess.get(url, headers=self.header, timeout=self.TIMEOUT)
                if r.status_code < 400:
                    if not r.encoding:          # 接口没带 charset，强制 UTF-8 防乱码
                        r.encoding = "utf-8"
                    return r.text
            except Exception:
                pass
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=self.header)
            resp = urllib.request.urlopen(req, timeout=self.TIMEOUT)
            try:
                return resp.read().decode("utf-8", "ignore")
            finally:
                resp.close()
        except Exception:
            return ""

    def _json(self, url):
        txt = self._get(url)
        if not txt:
            return None
        try:
            d = json.loads(txt)
        except Exception:
            return None
        return d if isinstance(d, dict) else None

    # ------------------------------------------------------------------
    # URL 拼装
    # ------------------------------------------------------------------
    def _enc(self, v):
        """站的 encode() 规则：空 -> '~'，非空 -> 'v'+encodeURIComponent(v)"""
        v = str(v if v is not None else "").strip()
        return "v" + quote(v, safe="") if v else "~"

    def _list_url(self, tid, sort, pg, genre="", region="", year="", form=""):
        return "%s/data/%s/list/%s/%s/%d/%s/%s/%s/%s.json" % (
            self.API, self.VER, tid, sort, pg,
            self._enc(genre), self._enc(region), self._enc(year), self._enc(form))

    def _search_url(self, key, pg, tid="all", sort="relevance"):
        return "%s/data/%s/search/%s/%s/%d/%s/~/~/~/~.json" % (
            self.API, self.VER, tid, sort, pg, self._enc(key))

    # ------------------------------------------------------------------
    # 分类 / 筛选
    # ------------------------------------------------------------------
    def _meta_data(self):
        if self._meta is not None:
            return self._meta
        d = self._json("%s/data/%s/meta.json" % (self.API, self.VER)) or {}
        cats = []
        for c in d.get("categories") or []:
            if not isinstance(c, dict):
                continue
            slug = str(c.get("slug") or "").strip()
            name = str(c.get("name") or "").strip()
            if not slug or not name:
                continue
            cats.append({
                "slug": slug,
                "name": name,
                "genres": [str(x) for x in (c.get("genres") or [])],
                "regions": [str(x) for x in (c.get("regions") or [])],
                "years": [str(x) for x in (c.get("years") or [])][:40],
                "forms": [str(x) for x in (c.get("forms") or [])],
            })
        if not cats:                            # 接口挂了也能进站
            cats = [{"slug": s, "name": n, "genres": [], "regions": [], "years": [], "forms": []}
                    for s, n in (("movie", "电影"), ("tv", "电视剧"), ("variety", "综艺"),
                                 ("animation", "动漫"), ("short", "短剧"),
                                 ("documentary", "纪录片"), ("science", "科普"),
                                 ("sport", "体育"), ("trailer", "预告片"),
                                 ("commentary", "影视解说"), ("ethics", "伦理"))]
        self._meta = cats
        return cats

    def _filters(self):
        out = {}
        for c in self._meta_data():
            fl = []
            if c["slug"] in self.HOT_SLUGS:
                sorts = [{"n": "热门", "v": "hot"}, {"n": "最新", "v": "latest"},
                         {"n": "年份", "v": "year"}]
            else:
                sorts = [{"n": "最新", "v": "latest"}, {"n": "年份", "v": "year"}]
            fl.append({"key": "sort", "name": "排序", "value": sorts})
            fields = (("genre", "项目" if c["slug"] == "sport" else "题材", c["genres"]),
                      ("region", "地区", c["regions"]),
                      ("year", "年份", c["years"]),
                      ("form", "联赛" if c["slug"] == "sport" else "形态", c["forms"]))
            for key, label, vals in fields:
                if vals:
                    fl.append({"key": key, "name": label,
                               "value": [{"n": "全部", "v": ""}] +
                                        [{"n": v, "v": v} for v in vals]})
            out[c["slug"]] = fl
        return out

    # ------------------------------------------------------------------
    # 条目
    # ------------------------------------------------------------------
    def _item(self, it):
        if not isinstance(it, dict):
            return None
        if it.get("external") or it.get("playable") is False:   # 豆瓣外链，没有片源
            return None
        vid = str(it.get("id") or "").strip()
        title = str(it.get("title") or "").strip()
        if not vid.isdigit() or not title:
            return None
        vod = {
            "vod_id": vid,
            "vod_name": title,
            "vod_pic": str(it.get("poster") or ""),
            "vod_remarks": str(it.get("progress") or ""),
            "vod_year": str(it.get("year") or ""),
            "vod_area": " ".join(str(it.get("region") or "").split()),
            "type_name": str(it.get("category") or ""),
        }
        if it.get("directors"):
            vod["vod_director"] = str(it["directors"])
        if it.get("actors"):
            vod["vod_actor"] = str(it["actors"])
        if it.get("summary"):
            vod["vod_content"] = str(it["summary"])
        return vod

    def _items(self, data):
        out, seen = [], set()
        for it in (data or {}).get("items") or []:
            vod = self._item(it)
            if vod and vod["vod_id"] not in seen:
                seen.add(vod["vod_id"])
                out.append(vod)
        return out

    def _pager(self, d, pg, vlist):
        try:
            pagecount = int(d.get("pages") or 1)
        except Exception:
            pagecount = 1
        try:
            total = int(d.get("total") or len(vlist))
        except Exception:
            total = len(vlist)
        try:
            limit = int(d.get("page_size") or self.LIMIT)
        except Exception:
            limit = self.LIMIT
        return {"list": vlist, "page": pg, "pagecount": max(pagecount, 1),
                "limit": limit, "total": total}

    def _home_list(self):
        if self._home is not None:
            return self._home
        out, seen = [], set()
        d = self._json("%s/data/%s/home.json" % (self.API, self.VER)) or {}
        for sec in d.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            for it in sec.get("items") or []:
                vod = self._item(it)
                if vod and vod["vod_id"] not in seen:
                    seen.add(vod["vod_id"])
                    out.append(vod)
        if not out:
            out = self._items(self._json(self._list_url("movie", "hot", 1)))
        self._home = out
        return out

    # ------------------------------------------------------------------
    # TVBox 内容接口
    # ------------------------------------------------------------------
    def homeContent(self, filter=None):
        return {
            "class": [{"type_id": c["slug"], "type_name": c["name"]}
                      for c in self._meta_data()],
            "filters": self._filters(),
            "list": self._home_list(),
        }

    def homeVideoContent(self):
        return {"list": self._home_list()}

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        tid = str(tid or "movie").strip() or "movie"
        ext = extend if isinstance(extend, dict) else {}
        sort = str(ext.get("sort") or "").strip()
        if sort not in ("hot", "latest", "year"):
            sort = "hot" if tid in self.HOT_SLUGS else "latest"
        d = self._json(self._list_url(tid, sort, pg, ext.get("genre"),
                                      ext.get("region"), ext.get("year"),
                                      ext.get("form"))) or {}
        return self._pager(d, pg, self._items(d))

    def searchContent(self, key, quick=False, pg="1"):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        key = str(key or "").strip()
        if not key:
            return {"list": [], "page": 1, "pagecount": 1, "limit": self.LIMIT, "total": 0}
        d = self._json(self._search_url(key, pg)) or {}
        return self._pager(d, pg, self._items(d))

    # ------------------------------------------------------------------
    # 详情 / 剧集
    # ------------------------------------------------------------------
    def _work(self, wid):
        wid = str(wid or "").strip()
        if not wid.isdigit():
            return {}
        if wid in self._work_cache:
            return self._work_cache[wid]
        d = self._json("%s/data/%s/work/%s.json" % (self.API, self.VER, wid)) or {}
        if len(self._work_cache) > 30:
            self._work_cache.clear()
        self._work_cache[wid] = d
        return d

    def _episodes(self, line):
        """返回 [(id, label, m3u8), ...]；优先 v2 直链，失败再试 v3 拼 origin"""
        if not isinstance(line, dict):
            return []
        for key in ("playlist", "playlist_v3"):
            url = str(line.get(key) or "").strip()
            if not url:
                continue
            d = self._json(url)
            if not d:
                continue
            items = d.get("items") or []
            if not items:
                continue
            origins = d.get("origins") or {}
            out = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                u = str(it.get("url") or "").strip()
                if not u:                                   # v3：origins + suffix
                    base = ""
                    if isinstance(origins, dict) and origins:
                        oid = it.get("origin_id")
                        if oid is not None:
                            base = origins.get(str(oid)) or origins.get(oid) or ""
                        if not base:
                            base = list(origins.values())[0]
                    suf = str(it.get("suffix") or "")
                    if base and suf:
                        u = base.rstrip("/") + (suf if suf.startswith("/") else "/" + suf)
                if u:
                    out.append((str(it.get("id") or ""), str(it.get("label") or ""), u))
            if out:
                return out
        return []

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) and ids else ids
        vid = str(vid or "").strip()
        d = self._work(vid)
        if not d:
            return {"list": []}
        play_from, play_url = [], []
        for line in d.get("lines") or []:
            if not isinstance(line, dict):
                continue
            lid = str(line.get("id") or "").strip()
            if not lid:
                continue
            lname = str(line.get("name") or "").strip() or ("线路" + lid)
            try:
                count = int(line.get("count") or 0)
            except Exception:
                count = 0
            if count < 1:
                count = 1
            if count == 1:
                labels = ["正片"]
            else:
                labels = ["第%02d集" % i for i in range(1, count + 1)]
            # 这里只写占位，真正 m3u8 在 playerContent 里按 vid@线路@第几集 现取
            eps = ["%s$%s@%s@%d" % (lb, vid, lid, i)
                   for i, lb in enumerate(labels, 1)]
            play_from.append(lname.replace("$", "").replace("#", ""))
            play_url.append("#".join(eps))
        vod = {
            "vod_id": vid,
            "vod_name": str(d.get("title") or ""),
            "vod_pic": str(d.get("poster") or ""),
            "vod_year": str(d.get("year") or ""),
            "vod_area": " ".join(str(d.get("region") or "").split()),
            "vod_remarks": str(d.get("progress") or ""),
            "vod_director": str(d.get("directors") or ""),
            "vod_actor": str(d.get("actors") or ""),
            "vod_content": str(d.get("summary") or ""),
            "type_name": str(d.get("category") or ""),
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }
        return {"list": [vod]}

    def _play_url(self, wid, lid, idx):
        d = self._work(wid)
        lines = d.get("lines") or []
        line = None
        for ln in lines:
            if isinstance(ln, dict) and str(ln.get("id") or "") == str(lid):
                line = ln
                break
        if line is None and lines:
            line = lines[0]
        eps = self._episodes(line)
        if not eps:
            return ""
        if 1 <= idx <= len(eps):
            return eps[idx - 1][2]
        return eps[0][2]

    def playerContent(self, flag, ids, vipFlags=None):
        raw = ids[0] if isinstance(ids, (list, tuple)) and ids else ids
        raw = str(raw or "").strip()
        header = dict(self.header)
        if not raw:
            return {"parse": 0, "playUrl": "", "url": "", "header": header}
        if raw.startswith("http") and self.isVideoFormat(raw):
            return {"parse": 0, "playUrl": "", "url": raw, "header": header}
        m = re.match(r"^(\d+)@(\d+)@(\d+)$", raw)
        if m:
            return {"parse": 0, "playUrl": "",
                    "url": self._play_url(m.group(1), m.group(2), int(m.group(3))),
                    "header": header}
        if raw.isdigit():                       # 只给了影片 id，给第一条线路第一集
            d = self._work(raw)
            lines = d.get("lines") or []
            if lines:
                eps = self._episodes(lines[0])
                if eps:
                    return {"parse": 0, "playUrl": "", "url": eps[0][2], "header": header}
        return {"parse": 0, "playUrl": "", "url": "", "header": header}

    # ------------------------------------------------------------------
    # 本地代理（播放器不带 Referer 时兜底）
    # ------------------------------------------------------------------
    def localProxy(self, param):
        url = param.get("url", "") if isinstance(param, dict) else ""
        if not url:
            return [403, "text/plain", b"", None]
        if self._sess is not None:
            try:
                r = self._sess.get(url, headers=self.header, timeout=self.TIMEOUT)
                return [r.status_code,
                        r.headers.get("Content-Type", "application/octet-stream"),
                        r.content, None]
            except Exception:
                pass
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=self.header)
            resp = urllib.request.urlopen(req, timeout=self.TIMEOUT)
            try:
                return [200,
                        resp.headers.get("Content-Type", "application/octet-stream"),
                        resp.read(), None]
            finally:
                resp.close()
        except Exception:
            return [403, "text/plain", b"", None]


# ----------------------------------------------------------------------
# 电脑上直接跑：python X影视.py
# ----------------------------------------------------------------------
if __name__ == "__main__":
    sp = Spider()
    sp.init("")
    hc = sp.homeContent(True)
    print("分类:", [c["type_name"] for c in hc["class"]])
    print("首页条数:", len(hc["list"]))
    cc = sp.categoryContent("tv", 1, False, {"sort": "hot"})
    print("电视剧第1页:", cc["total"], "条 /", cc["pagecount"], "页")
    if cc["list"]:
        print("  首条:", cc["list"][0]["vod_name"], cc["list"][0]["vod_remarks"])
    sc = sp.searchContent("仙逆", False, "1")
    print("搜索仙逆:", sc["total"], "条")
    if sc["list"]:
        print("  首条:", sc["list"][0]["vod_name"], sc["list"][0]["vod_id"])
    vid = (sc["list"] or cc["list"])[0]["vod_id"]
    dc = sp.detailContent([vid])
    vod = dc["list"][0]
    froms = vod["vod_play_from"].split("$$$")
    print("详情:", vod["vod_name"], "/ 线路数:", len(froms), "/ 首线路:", froms[0] if froms else "-")
    first = vod["vod_play_url"].split("$$$")[0].split("#")[0]
    print("首集占位:", first)
    pc = sp.playerContent(froms[0] if froms else "", first.split("$")[-1], None)
    print("解析出直链:", pc["url"])

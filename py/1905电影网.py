# coding=utf-8
"""
1905电影网 · TVBox Python 爬虫
================================================================
站点 : https://www.1905.com
分类 : 电影 / 微电影 / 系列电影 / 纪录片 / 晚会 / 独家 / 综艺

为什么还要写 py
----------------------------------------------------------------
1. 站点 WAF 按 UA 放行：PC / iPhone UA 一律 403，只有 Android UA 能过；
2. 列表页是服务端渲染的（好抓），但播放页 HTML 里没有任何 mp4/m3u8，
   直链由播放器 JS 调 m.1905.com/api/video/mediainfo2.php 运行时生成：

       k = apikey[10:18]
       t = 毫秒时间戳
       p = 15 位随机数
       s = md5(k + t + id + p)

   本爬虫把这条签名复现了，因此不用浏览器也能拿到 mp4。
3. 非会员/未登录时接口只给 8 分钟试看片段（clipTo=480000）；
   若在 ext 里带上会员 Cookie，接口可能返回完整片长（视账号权限而定）。

TVBox 接入
----------------------------------------------------------------
    {"key":"1905电影网","name":"1905电影网","type":3,
     "api":"./py/1905电影网.py","searchable":1,"quickSearch":1,
     "filterable":1,"ext":""}

可选 ext（JSON 字符串或对象）：

    {"cookie": "PHPSESSID=...; mauth=...", "u": "b2332d86"}

    cookie：1905 会员/登录 Cookie，不带就只能看试看片段
    u     ：播放器设备号，默认 b2332d86（站方网页里的常量）

本地自测
----------------------------------------------------------------
    python 1905电影网.py
"""

import base64
import hashlib
import json
import random
import re
import time
from urllib.parse import quote

try:
    import requests as _requests
except Exception:                                   # TVBox 里通常没有 requests
    _requests = None


class Spider(object):

    SITE = "https://www.1905.com"
    API = "https://m.1905.com"
    UA = ("Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")
    TIMEOUT = 15
    LIMIT = 24

    # (cateId, 名称)——cateId 直接拼进 /vod/list/{cateId}/o3p{pg}.html
    CATS = [
        ("n_1", "电影"),
        ("c_922", "微电影"),
        ("n_2", "系列电影"),
        ("c_927", "纪录片"),
        ("c_586", "晚会"),
        ("c_178", "独家"),
        ("c_1024", "综艺"),
    ]

    SORTS = [("o3", "最热"), ("o1", "最新"), ("o4", "好评")]

    AREAS = [("", "全部"), ("a_1", "内地"), ("a_2", "港台"), ("a_4", "欧美")]

    TYPES = [
        ("", "全部"), ("t_1", "动作"), ("t_2", "喜剧"), ("t_3", "爱情"),
        ("t_4", "冒险"), ("t_5", "文艺"), ("t_6", "惊悚"), ("t_8", "战争"),
        ("t_9", "剧情"), ("t_10", "科幻"), ("t_12", "家庭"), ("t_13", "警匪"),
        ("t_14", "运动"), ("t_15", "神话"), ("t_16", "武侠"), ("t_19", "乡村"),
        ("t_20", "动画"), ("t_21", "儿童"), ("t_24", "传记"), ("t_25", "灾难"),
        ("t_27", "青春"), ("t_29", "女性"), ("t_30", "经典"), ("t_31", "系列"),
        ("t_33", "悬疑"), ("t_35", "历史"),
    ]

    def __init__(self):
        self.name = "1905电影网"
        self.header = {
            "User-Agent": self.UA,
            "Referer": self.SITE + "/",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        self._sess = _requests.Session() if _requests else None
        self._page_cache = {}
        self._key_cache = {}
        self.cookie = ""
        self.device = "b2332d86"

    # ------------------------------------------------------------------
    # TVBox 固定接口
    # ------------------------------------------------------------------
    def getName(self):
        return self.name

    def getDependence(self):
        return []

    def manualVideoCheck(self):
        return False

    def init(self, extend=""):
        ext = {}
        if isinstance(extend, dict):
            ext = extend
        elif isinstance(extend, str) and extend.strip():
            try:
                ext = json.loads(extend)
            except Exception:
                ext = {}
        if isinstance(ext, dict):
            self.cookie = str(ext.get("cookie") or "").strip()
            self.device = str(ext.get("u") or self.device).strip() or "b2332d86"

    def action(self, action):
        return ""

    def destroy(self):
        if self._sess is not None:
            try:
                self._sess.close()
            except Exception:
                pass

    def isVideoFormat(self, url):
        u = str(url or "").lower()
        return ".m3u8" in u or ".mp4" in u

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _headers(self, referer=None):
        h = dict(self.header)
        if referer:
            h["Referer"] = referer
        if self.cookie:
            h["Cookie"] = self.cookie
        return h

    def _get(self, url, referer=None):
        headers = self._headers(referer)
        if self._sess is not None:
            r = self._sess.get(url, headers=headers, timeout=self.TIMEOUT)
            r.encoding = "utf-8"
            return r.text
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=self.TIMEOUT)
        try:
            return resp.read().decode("utf-8", "ignore")
        finally:
            resp.close()

    def _jsonp(self, txt):
        t = str(txt or "").strip()
        if t.startswith("(") and t.endswith(")"):
            t = t[1:-1]
        elif t and not t.startswith("{"):
            a, b = t.find("("), t.rfind(")")
            if a >= 0 and b > a:
                t = t[a + 1:b]
        return json.loads(t)

    # ------------------------------------------------------------------
    # 列表页
    # ------------------------------------------------------------------
    def _list_url(self, tid, pg, sort="o3", tp="", area=""):
        seg = str(tid or "n_1").strip() or "n_1"
        if tp:
            seg += "_" + tp
        if area:
            seg += "_" + area
        return "%s/vod/list/%s/%sp%s.html" % (self.SITE, seg, sort, pg)

    def _clean(self, s):
        s = re.sub(r"<[^>]+>", "", str(s or ""))
        return re.sub(r"\s+", " ", s).strip()

    def _items(self, html):
        out, seen = [], set()
        for chunk in re.findall(r'<a class="pic-pack-outer"([\s\S]*?)</a>', html or ""):
            href = re.search(r'href="([^"]+)"', chunk)
            title = re.search(r'title="([^"]+)"', chunk)
            pic = re.search(r'src="([^"]+)"', chunk)
            if not href or not title:
                continue
            m = re.search(r"/vod/play/(\d+)\.shtml", href.group(1))
            if not m:
                continue
            vid = m.group(1)
            if vid in seen:
                continue
            seen.add(vid)
            sub = re.search(r"<p>([\s\S]*?)</p>", chunk)
            out.append({
                "vod_id": "1905:" + vid,
                "vod_name": self._clean(title.group(1)),
                "vod_pic": pic.group(1) if pic else "",
                "vod_remarks": self._clean(sub.group(1)) if sub else "",
            })
        return out

    def _filters(self):
        out = {}
        for tid, _ in self.CATS:
            fl = [{"key": "sort", "name": "排序",
                   "value": [{"n": n, "v": v} for v, n in self.SORTS]}]
            if tid == "n_1":                        # 类型/地区只给「电影」，避免空分类
                fl.append({"key": "type", "name": "类型",
                           "value": [{"n": n, "v": v} for v, n in self.TYPES]})
                fl.append({"key": "area", "name": "地区",
                           "value": [{"n": n, "v": v} for v, n in self.AREAS]})
            out[tid] = fl
        return out

    # ------------------------------------------------------------------
    # 搜索：站内搜索页是「影片实体页」，取其中的最新视频（免费、可直接播）
    # ------------------------------------------------------------------
    def _search_movie(self, html):
        """搜索页顶部的影片卡：只有播放键指向 /vod/play/ 时才算可播条目"""
        m = re.search(
            r'<div class="movie_box[\s\S]*?<h2 class="title-mv"[\s\S]*?<a[^>]*>([\s\S]*?)</a>'
            r'[\s\S]*?<img[^>]+src="([^"]+)"[\s\S]*?href="([^"]+)"[^>]*class="btn-play"',
            html or "")
        if not m:
            return []
        vm = re.search(r"/vod/play/(\d+)\.shtml", m.group(3))
        if not vm:
            return []
        title = self._clean(m.group(1))
        title = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
        if not title:
            return []
        return [{
            "vod_id": "1905:" + vm.group(1),
            "vod_name": title,
            "vod_pic": m.group(2),
            "vod_remarks": "影片",
        }]

    def _search_items(self, html):
        out, seen = [], set()
        for chunk in re.findall(r'<div class="video_box([\s\S]*?)</div>', html or ""):
            href = re.search(r'href="([^"]+)"', chunk)
            title = re.search(r'title="([^"]+)"', chunk)
            pic = re.search(r'src="([^"]+)"', chunk)
            if not href or not title:
                continue
            m = re.search(r"/video/play/(\d+)\.shtml", href.group(1))
            if not m:
                continue
            vid = m.group(1)
            if vid in seen:
                continue
            seen.add(vid)
            out.append({
                "vod_id": "video:" + vid,
                "vod_name": self._clean(title.group(1)),
                "vod_pic": pic.group(1) if pic else "",
                "vod_remarks": "视频",
            })
        return out

    # ------------------------------------------------------------------
    # 详情页
    # ------------------------------------------------------------------
    def _page(self, kind, vid):
        key = kind + ":" + vid
        if key in self._page_cache:
            return self._page_cache[key]
        path = "/vod/play/" if kind == "vod" else "/video/play/"
        html = self._get(self.SITE + path + vid + ".shtml")
        if len(self._page_cache) > 40:
            self._page_cache.clear()
        self._page_cache[key] = html
        return html

    def _config(self, html):
        cfg = {}
        m = re.search(r"VIDEOCONFIG\s*=\s*\{([\s\S]*?)\}\s*</script>", html or "")
        body = m.group(1) if m else (html or "")
        for k, v in re.findall(r'(\w+)\s*:\s*"([^"]*)"', body):
            cfg[k] = v
        for k, v in re.findall(r"(\w+)\s*:\s*'([^']*)'", body):
            cfg.setdefault(k, v)
        return cfg

    def _desc(self, html):
        m = re.search(r'<meta name="description" content="([^"]*)"', html or "")
        return self._clean(m.group(1)) if m else ""

    def detailContent(self, ids):
        raw = ids[0] if isinstance(ids, (list, tuple)) and ids else ids
        raw = str(raw or "").strip()
        if not raw:
            return {"list": []}
        kind = "video" if raw.startswith("video:") else "vod"
        vid = raw.split(":", 1)[1] if ":" in raw else raw
        if not vid.isdigit():
            return {"list": []}
        html = self._page(kind, vid)
        cfg = self._config(html)
        name = cfg.get("title") or cfg.get("contentname") or ""
        if not name:
            m = re.search(r"<title>(.*?)</title>", html or "")
            name = self._clean(m.group(1).split("-")[0]) if m else vid
        pic = cfg.get("thumb") or ""
        dur = cfg.get("duration") or cfg.get("play_duration") or ""
        remark = ""
        try:
            sec = int(dur)
            if sec > 0:
                remark = "%d分钟" % max(1, sec // 60)
        except Exception:
            pass
        if kind == "vod" and not self.cookie:
            remark = (remark + " · 试看") if remark else "试看"
        vod = {
            "vod_id": raw,
            "vod_name": name,
            "vod_pic": pic,
            "vod_remarks": remark,
            "vod_content": self._desc(html),
            "vod_play_from": "1905视频" if kind == "video" else "1905",
            "vod_play_url": "正片$" + raw,
        }
        return {"list": [vod]}

    # ------------------------------------------------------------------
    # 取流
    # ------------------------------------------------------------------
    def _apikey(self, vid):
        if vid in self._key_cache:
            return self._key_cache[vid]
        html = self._page("vod", vid)
        m = re.search(r'apikey\s*:\s*"([0-9a-f]{32})"', html or "")
        key = m.group(1) if m else ""
        self._key_cache[vid] = key
        return key

    def _media_vod(self, vid):
        """VOD：k/t/p/s 签名 → mediainfo2.php → base64 mp4（非会员为试看片段）"""
        apikey = self._apikey(vid)
        if len(apikey) < 18:
            return ""
        k = apikey[10:18]
        t = str(int(time.time() * 1000))
        p = str(random.randint(10 ** 14, 10 ** 15 - 1))
        s = hashlib.md5((k + t + vid + p).encode()).hexdigest()
        url = ("%s/api/video/mediainfo2.php?id=%s&type=0&fr=www&source_key=mcfile"
               "&k=%s&t=%s&i=%s&p=%s&d=false&r=true&u=%s&s=%s&v=1"
               % (self.API, vid, k, t, vid, p, self.device, s))
        d = self._jsonp(self._get(url, referer="%s/vod/play/%s.shtml" % (self.SITE, vid)))
        return base64.b64decode(d.get("iosurl") or b"").decode("utf-8", "ignore")

    def _media_video(self, vid):
        """免费短视频：getmediainfo2.php 不需要签名"""
        url = ("%s/api/video/getmediainfo2.php?id=%s&type=1&fr=www"
               "&source_key=mcfile&isall=1" % (self.API, vid))
        d = self._jsonp(self._get(url, referer="%s/video/play/%s.shtml" % (self.SITE, vid)))
        return base64.b64decode(d.get("iosurl") or b"").decode("utf-8", "ignore")

    def playerContent(self, flag, ids, vipFlags=None):
        raw = ids[0] if isinstance(ids, (list, tuple)) and ids else ids
        raw = str(raw or "").strip()
        header = self._headers()
        url = ""
        try:
            if raw.startswith("http"):
                url = raw
            elif raw.startswith("video:"):
                url = self._media_video(raw.split(":", 1)[1])
            else:
                vid = raw.split(":", 1)[1] if raw.startswith("1905:") else raw
                if vid.isdigit():
                    url = self._media_vod(vid)
        except Exception:
            url = ""
        if url.startswith("http://") and "vodfile.m1905.com" in url:
            url = "https://" + url[7:]
        return {"parse": 0, "playUrl": "", "url": url, "header": header}

    # ------------------------------------------------------------------
    # 内容接口
    # ------------------------------------------------------------------
    def homeContent(self, filter=None):
        try:
            first = self._items(self._get(self._list_url("n_1", 1)))
        except Exception:
            first = []
        return {
            "class": [{"type_id": tid, "type_name": name} for tid, name in self.CATS],
            "filters": self._filters(),
            "list": first,
        }

    def homeVideoContent(self):
        try:
            return {"list": self._items(self._get(self._list_url("n_1", 1)))}
        except Exception:
            return {"list": []}

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        ext = extend if isinstance(extend, dict) else {}
        sort = str(ext.get("sort") or "o3").strip()
        if sort not in ("o1", "o3", "o4"):
            sort = "o3"
        tp = str(ext.get("type") or "").strip()
        area = str(ext.get("area") or "").strip()
        if tp and not re.match(r"^t_\d+$", tp):
            tp = ""
        if area and not re.match(r"^a_\d+$", area):
            area = ""
        url = self._list_url(tid, pg, sort, tp, area)
        try:
            vlist = self._items(self._get(url))
        except Exception:
            vlist = []
        return {
            "list": vlist,
            "page": pg,
            "pagecount": pg + 1 if len(vlist) >= self.LIMIT else pg,
            "limit": self.LIMIT,
            "total": self.LIMIT * pg,
        }

    def searchContent(self, key, quick=False, pg="1"):
        key = str(key or "").strip()
        if not key:
            return {"list": [], "page": 1, "pagecount": 1, "limit": self.LIMIT, "total": 0}
        try:
            html = self._get(self.SITE + "/search/?q=" + quote(key, safe=""))
            vlist = self._search_movie(html) + self._search_items(html)
        except Exception:
            vlist = []
        return {
            "list": vlist,
            "page": 1,
            "pagecount": 1,
            "limit": self.LIMIT,
            "total": len(vlist),
        }


# ----------------------------------------------------------------------
# 电脑上直接跑
# ----------------------------------------------------------------------
if __name__ == "__main__":
    sp = Spider()
    sp.init("")
    hc = sp.homeContent(True)
    print("分类:", [c["type_name"] for c in hc["class"]])
    print("首页条数:", len(hc["list"]))

    cc = sp.categoryContent("n_1", 1, False, {"sort": "o3"})
    print("电影第1页:", len(cc["list"]), "条")
    if cc["list"]:
        print("  首条:", cc["list"][0]["vod_name"], cc["list"][0]["vod_id"])

    cc2 = sp.categoryContent("n_1", 1, False, {"sort": "o1", "type": "t_8", "area": "a_1"})
    print("战争/内地/最新:", len(cc2["list"]), "条", cc2["list"][0]["vod_name"] if cc2["list"] else "-")

    sc = sp.searchContent("流浪地球", False, "1")
    print("搜索:", len(sc["list"]), "条")
    if sc["list"]:
        print("  首条:", sc["list"][0]["vod_name"], sc["list"][0]["vod_id"])

    if cc["list"]:
        vid = cc["list"][0]["vod_id"]
        vod = sp.detailContent([vid])["list"][0]
        print("详情:", vod["vod_name"], "/", vod["vod_play_url"])
        pc = sp.playerContent("1905", vod["vod_play_url"].split("$")[-1], None)
        print("播放:", pc["url"][:110])

    if sc["list"]:
        vid = sc["list"][0]["vod_id"]
        vod = sp.detailContent([vid])["list"][0]
        pc = sp.playerContent("1905视频", vod["vod_play_url"].split("$")[-1], None)
        print("短视频:", vod["vod_name"], "->", pc["url"][:110])

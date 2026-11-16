#!/usr/bin/env python3
"""Pilot: historical daily whole-market quotes; resumable, validates stock rows.
Only daily closing prices, not intraday; no investment advice.
"""
import datetime as dt
import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from update import DATA, standard, score

TZ = dt.timezone(dt.timedelta(hours=8))
TODAY = dt.datetime.now(TZ).date()
START = TODAY - dt.timedelta(days=145)
END = TODAY - dt.timedelta(days=1)
BATCH = min(20, max(1, int(os.getenv('BACKFILL_BATCH', '10'))))
STATE = DATA / 'backfill_state.json'
HISTORY = DATA / 'history.json'
LATEST = DATA / 'latest.json'
HEADERS = {'User-Agent': 'Mozilla/5.0 TaiwanStockResearch/1.0','Accept':'application/json'}

def request(url):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=35) as r:
                return json.loads(r.read().decode('utf-8-sig'))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            code = getattr(exc, 'code', None)
            # CDN / TLS handshake issues (525/526) are transient upstream failures.
            retryable = code is None or code in (408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526)
            if not retryable or attempt == 3:
                raise
            delay = 5 * (attempt + 1)
            print(f'Upstream temporary error {exc}; retry {attempt + 1}/3 in {delay}s', file=sys.stderr)
            time.sleep(delay)

def table(payload):
    if not isinstance(payload,dict): raise ValueError('資料來源不是JSON物件')
    for t in payload.get('tables') or [payload]:
        fields=t.get('fields') or t.get('columns') or []
        rows=t.get('data') or t.get('aaData') or []
        names=[str(c.get('title',c.get('name','')) if isinstance(c,dict) else c).strip() for c in fields]
        if any('代號' in s for s in names) and any('收盤' in s for s in names):
            return [dict(zip(names,row)) for row in rows if isinstance(row,list)]
    raise ValueError('找不到有欄名的股票收盤資料表')

def listed(day):
    query=urllib.parse.urlencode({'response':'json','date':day.strftime('%Y%m%d'),'type':'ALLBUT0999'})
    data=request('https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?'+query)
    if data.get('stat') not in ('OK','ok'): return []
    return table(data)

def otc(day):
    query=urllib.parse.urlencode({'date':day.strftime('%Y/%m/%d'),'type':'EW','response':'json'})
    data=request('https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?'+query)
    if data.get('stat') not in ('OK','ok',None): return []
    return table(data)

def main():
    DATA.mkdir(exist_ok=True)
    progress=json.loads(STATE.read_text('utf8')) if STATE.exists() else {}
    history=json.loads(HISTORY.read_text('utf8')) if HISTORY.exists() else {'stocks':{}}
    stocks=history.get('stocks',{})
    cursor=dt.date.fromisoformat(progress.get('nextDate',START.isoformat()))
    if cursor>END:
        print('日期範圍已經掃描完畢；請檢查成功率及每股歷史天數')
        return
    dates=[cursor+dt.timedelta(days=i) for i in range(BATCH)]
    dates=[d for d in dates if d<=END]
    errors=[]; amounts={'上市':0,'上櫃':0}
    for day in dates:
        if day.weekday()>=5: continue
        for market,fetcher in (('上市',listed),('上櫃',otc)):
            try:
                raw=fetcher(day)
                if not raw: continue
                items=[]
                for r in raw:
                    x=dict(r); x['Date']=day.isoformat()
                    item=standard(x,market)
                    if item and item['date']==day.isoformat(): items.append(item)
                if len(items)<250: raise ValueError(f'合格股票僅 {len(items)}/{len(raw)}，資料格式可能改變')
                for x in items:
                    key=market+':'+x['id']
                    rec=stocks.setdefault(key,{'id':x['id'],'name':x['name'],'market':market,'bars':[]})
                    rec['name']=x['name']
                    bars={b['date']:b for b in rec.get('bars',[])}
                    bars[x['date']]={k:x[k] for k in ('date','close','high','low','volume','value','change')}
                    rec['bars']=sorted(bars.values(),key=lambda b:b['date'])[-155:]
                amounts[market]+=len(items)
            except Exception as e:
                msg=f'{day} {market}: {e}'
                errors.append(msg); print('ERROR:',msg,file=sys.stderr)
            time.sleep(1.2)
    nextday=min((dt.date.fromisoformat(e[:10]) for e in errors),default=dates[-1]+dt.timedelta(days=1))
    progress={'nextDate':nextday.isoformat(),'rangeStart':START.isoformat(),'rangeEnd':END.isoformat(),
              'lastAttempt':dt.datetime.now(TZ).isoformat(timespec='seconds'),
              'acceptedThisRun':amounts,'errors':errors}
    HISTORY.write_text(json.dumps({'stocks':stocks},ensure_ascii=False,separators=(',',':')),'utf8')
    STATE.write_text(json.dumps(progress,ensure_ascii=False,indent=2),'utf8')
    previous=json.loads(LATEST.read_text('utf8')) if LATEST.exists() else {}
    report=[score(r) for r in stocks.values() if r.get('bars')]
    report.sort(key=lambda s:(s['score'] is not None,s['score'] or 0,s['volumeRatio'] or 0),reverse=True)
    previous.update({'generatedAt':dt.datetime.now(TZ).isoformat(timespec='seconds'),'backfill':progress,'stocks':report})
    LATEST.write_text(json.dumps(previous,ensure_ascii=False,separators=(',',':')),'utf8')
    print('Scanned:',len(dates),'calendar days; accepted:',amounts,'stocks:',len(report),'errors:',len(errors))
    if errors: raise RuntimeError('回補有來源或格式錯誤；請查閱工作紀錄，修正後重跑')

if __name__=='__main__': main()

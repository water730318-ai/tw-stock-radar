#!/usr/bin/env python3
"""Collect official latest daily close snapshots for TWSE & TPEx; no intraday claims."""
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
SOURCES = {
    '上市': 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
    '上櫃': 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes',
}

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 TaiwanStockResearch/1.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=35) as resp:
        payload = resp.read()
    parsed = json.loads(payload.decode('utf-8-sig'))
    if isinstance(parsed, list): return parsed
    if isinstance(parsed, dict):
        for key in ('data','Data','result','results'):
            if isinstance(parsed.get(key), list): return parsed[key]
    raise ValueError('來源不是預期的 JSON 陣列')

def pick(row, *keys):
    for key in keys:
        if key in row and row[key] not in (None, ''):
            return row[key]
    return None

def num(v):
    if v is None or v in ('--', '---', '-', '除權息', 'N/A'): return None
    s = str(v).replace(',', '').replace('+','').replace('%','').replace('元','').strip()
    s = s.replace('−','-').replace('－','-')
    s = re.sub(r'<[^>]+>', '', s).strip()
    try: return float(s)
    except (ValueError,TypeError): return None

def dateparse(v):
    if v is None: return None
    s = str(v).strip()
    if re.fullmatch(r'\d{7}',s):
        return f'{int(s[:3])+1911:04d}-{s[3:5]}-{s[5:]}'
    if re.fullmatch(r'\d{8}',s):
        year=int(s[:4]); return f'{year if year>1900 else year+1911:04d}-{s[4:6]}-{s[6:]}'
    parts = re.findall(r'\d+',s)
    if len(parts)>=3:
        yr=int(parts[0]); yr = yr+1911 if yr<1911 else yr
        return f'{yr:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}'
    return None

def standard(row, market):
    if market=='上市':
        code=pick(row,'Code','證券代號')
        name=pick(row,'Name','證券名稱')
        date=dateparse(pick(row,'Date','日期'))
        close=num(pick(row,'ClosingPrice','收盤價'))
        high=num(pick(row,'HighestPrice','最高價'))
        low=num(pick(row,'LowestPrice','最低價'))
        volume=num(pick(row,'TradeVolume','成交股數'))
        value=num(pick(row,'TradeValue','成交金額'))
        change=num(pick(row,'Change','漲跌價差'))
    else:
        code=pick(row,'SecuritiesCompanyCode','SecuritiesCompany','Code','股票代號','證券代號','代號')
        name=pick(row,'CompanyName','Name','股票名稱','證券名稱','名稱')
        date=dateparse(pick(row,'Date','日期','資料日期'))
        close=num(pick(row,'Close','ClosingPrice','ClosePrice','收盤','收盤價'))
        high=num(pick(row,'High','HighestPrice','最高','最高價'))
        low=num(pick(row,'Low','LowestPrice','最低','最低價'))
        volume=num(pick(row,'TradingShares','TradeVolume','成交股數','成交量'))
        value=num(pick(row,'TransactionAmount','TradeValue','成交金額'))
        change=num(pick(row,'Change','漲跌','漲跌價差'))
    code = str(code or '').strip()
    if not re.fullmatch(r'\d{4}', code): return None  # exclude warrants, ETFs and non-standard symbols
    if not name or not date or not close or close<=0 or volume is None or volume<0: return None
    if high is not None and low is not None and (low>high or high+1e-6<close or low-1e-6>close): return None
    return {'id':code,'name':str(name).strip(),'market':market,'date':date,'close':close,'high':high,'low':low,'volume':volume,'value':value,'change':change}

def sma(vals, n):
    return sum(vals[-n:])/n if len(vals)>=n else None

def ema_series(vals,period):
    if len(vals)<period: return []
    out=[None]*(period-1)
    e=sum(vals[:period])/period
    out.append(e)
    a=2/(period+1)
    for x in vals[period:]:
        e=a*x+(1-a)*e
        out.append(e)
    return out

def rsi(vals,n=14):
    if len(vals)<n+1: return None
    deltas=[vals[i]-vals[i-1] for i in range(1,len(vals))]
    gains=[max(x,0) for x in deltas]
    losses=[max(-x,0) for x in deltas]
    ag=sum(gains[:n])/n; al=sum(losses[:n])/n
    for i in range(n,len(deltas)):
        ag=(ag*(n-1)+gains[i])/n
        al=(al*(n-1)+losses[i])/n
    if al==0: return 100.0 if ag>0 else 50.0
    return 100-100/(1+ag/al)

def macd_hist(vals):
    if len(vals)<35: return None
    f=ema_series(vals,12); s=ema_series(vals,26)
    m=[f[i]-s[i] for i in range(25,len(vals))]
    signal=ema_series(m,9)
    if len(signal)<2: return None
    return round(m[-1]-signal[-1],4)

def score(rec):
    rows=rec['bars']; closes=[b['close'] for b in rows]; vols=[b['volume'] for b in rows]
    ma5=sma(closes,5); ma10=sma(closes,10); ma20=sma(closes,20); ma60=sma(closes,60)
    v20=sma(vols[:-1],20) if len(vols)>20 else None
    vr=vols[-1]/v20 if v20 and v20>0 else None
    peak=max([x['high'] or x['close'] for x in rows[-21:-1]]) if len(rows)>=21 else None
    rr=rsi(closes); mh=macd_hist(closes)
    price=closes[-1]; prev=closes[-2] if len(closes)>1 else None
    ret=(price/prev-1)*100 if prev else None
    # Avoid technical recommendations until enough historical bars exist.
    sufficient=len(rows)>=60
    points=0
    if sufficient:
        if price>ma20: points+=15
        if ma5>ma10>ma20>ma60: points+=25
        if ma20>ma60: points+=10
        if vr is not None and 1.3<=vr<=4: points+=15
        if peak and price>peak: points+=15
        if rr is not None and 50<=rr<=70: points+=10
        if mh is not None and mh>0: points+=10
    # Volume figures may come in shares or trading units, don't calculate lot count from raw until verified.
    extended=bool(ma20 and price>ma20*1.12) or bool(ret is not None and ret>=8.5)
    status='資料累積中' if not sufficient else ('過熱勿追' if extended else ('候選觀察' if points>=70 else ('持續觀察' if points>=50 else '暫不考慮')))
    risk_stop=round(min(ma20,price*0.94),2) if sufficient and ma20 else None
    risk_per_share=price-risk_stop if risk_stop is not None else None
    target=round(price+2*risk_per_share,2) if risk_per_share and risk_per_share>0 else None
    return {'id':rec['id'],'name':rec['name'],'market':rec['market'],'date':rows[-1]['date'],'close':price,'changePct':round(ret,2) if ret is not None else None,'volumeRatio':round(vr,2) if vr is not None else None,'ma5':round(ma5,2) if ma5 is not None else None,'ma10':round(ma10,2) if ma10 is not None else None,'ma20':round(ma20,2) if ma20 is not None else None,'ma60':round(ma60,2) if ma60 is not None else None,'rsi14':round(rr,1) if rr is not None else None,'macdHist':mh,'prev20High':round(peak,2) if peak is not None else None,'score':points if sufficient else None,'status':status,'stopReference':risk_stop,'target2R':target,'bars':len(rows)}

def main():
    DATA.mkdir(exist_ok=True)
    path=DATA/'history.json'
    old=json.loads(path.read_text(encoding='utf8')) if path.exists() else {'stocks':{}}
    stocks=old.get('stocks',{})
    errors={}; summary={}; fresh=[]
    for market,url in SOURCES.items():
        try:
            raw=fetch(url)
            normalized=[x for x in (standard(r,market) for r in raw) if x]
            dated={}
            for item in normalized: dated[item['date']]=dated.get(item['date'],0)+1
            if not dated: raise ValueError('資料欄位無法識別，沒有合格股票')
            newest=max(dated)
            selected=[x for x in normalized if x['date']==newest]
            if len(selected)<300: raise ValueError(f'僅 {len(selected)} 檔，拒絕覆蓋資料；請確認上游欄位')
            summary[market]={'date':newest,'count':len(selected)}
            fresh+=selected
        except Exception as e:
            errors[market]=str(e)
            print(f'{market} fetch failed: {e}',file=sys.stderr)
    if not fresh: raise RuntimeError('兩個官方來源均失敗，不更新排行榜')
    for r in fresh:
        key=f'{r["market"]}:{r["id"]}'
        rec=stocks.setdefault(key,{'id':r['id'],'name':r['name'],'market':r['market'],'bars':[]})
        rec['name']=r['name']
        bar={k:r[k] for k in ('date','close','high','low','volume','value','change')}
        by_date={x['date']:x for x in rec.get('bars',[])}
        by_date[r['date']]=bar
        rec['bars']=sorted(by_date.values(),key=lambda x:x['date'])[-130:]
    report=[score(rec) for rec in stocks.values() if rec.get('bars')]
    report.sort(key=lambda x:(x['score'] is not None,x['score'] or 0,x['volumeRatio'] or 0), reverse=True)
    now=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds')
    output={'generatedAt':now,'mode':'daily-close','coverage':summary,'errors':errors,'historicalTradingDaysNeeded':60,'stocks':report}
    (DATA/'latest.json').write_text(json.dumps(output,ensure_ascii=False,separators=(',',':')),encoding='utf8')
    path.write_text(json.dumps({'stocks':stocks},ensure_ascii=False,separators=(',',':')),encoding='utf8')
    print('Updated:',summary,'Errors:',errors,'Records:',len(report))

if __name__=='__main__': main()

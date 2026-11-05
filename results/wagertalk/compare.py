import json,glob,csv,os
# (wk, who, bb, game_id, side, line, show_result_recorded, note)
# side: away/home/over/under ; line is side-relative for spreads, total for totals
P=[
(1,'Marco',0,'49ers-rams','away',3.5,'W'),(1,'VR',0,'cowboys-giants','home',3,'W'),(1,'Marco',0,'falcons-steelers','away',3.5,'L'),
(1,'VR',0,'bears-panthers','home',3.5,'L'),(1,'Kelly',0,'browns-jaguars','away',8.5,'L'),(1,'Marco',0,'ravens-colts','home',3.5,'L'),
(1,'VR',1,'bills-texans','home',1.5,'L'),(1,'Marco',1,'commanders-eagles','home',-4.5,'L'),(1,'Kelly',1,'buccaneers-bengals','home',-4,'W'),
(2,'VR',0,'lions-bills','away',4,'L'),(2,'Marco',0,'colts-chiefs','away',6.5,'W'),(2,'Marco',0,'steelers-patriots','away',4.5,'L'),
(2,'Kelly',0,'commanders-cowboys','away',None,'L'),(2,'VR',0,'eagles-titans','home',7.5,'W'),(2,'Marco',0,'saints-ravens','home',-7.5,'L'),
(2,'VR',1,'vikings-bears','away',6,'W'),(2,'Marco',1,'jaguars-broncos','home',-2.5,'W'),(2,'Kelly',1,'bengals-texans','home',-2.5,'L'),
(3,'Marco',0,'eagles-bears','home',4.5,'W'),(3,'Marco',0,'seahawks-commanders','home',7,'W'),(3,'VR',0,'panthers-browns','home',3,'W'),
(3,'Kelly',0,'bengals-steelers','home',3.5,'W'),(3,'Marco',0,'patriots-jaguars','home',None,'W'),(3,'VR',1,'seahawks-commanders','home',7.5,'W'),
(3,'Marco',1,'texans-colts','away',None,'L'),(3,'Kelly',1,'ravens-cowboys','away',-3.5,'L'),
(4,'Marco',0,'lions-panthers','under',50.5,'L'),(4,'Marco',0,'patriots-bills','away',7,'W'),(4,'VR',0,'rams-eagles','home',3,'L'),
(4,'Kelly',0,'packers-buccaneers','home',4,'W'),(4,'Marco',0,'jets-bears','away',3.5,'L'),(4,'VR',1,'cowboys-texans','home',-2.5,'L'),
(4,'Marco',1,'cardinals-giants','home',None,'W'),(4,'Kelly',1,'broncos-49ers','away',3,'L'),
(5,'VR',0,'buccaneers-cowboys','away',10,'W'),(5,'Marco',0,'ravens-falcons','away',3,'W'),(5,'Kelly',0,'bills-rams','under',54.5,'L'),
(5,'Marco',0,'broncos-chargers','home',3.5,'L'),(5,'VR',0,'texans-titans','home',7.5,'L'),(5,'Kelly',0,'bears-packers','home',3,'W'),
(5,'Marco',0,'browns-jets','home',None,'W'),(5,'VR',1,'giants-commanders','home',-3.5,'L'),(5,'Marco',1,'vikings-saints','home',2,'L'),
(5,'Kelly',1,'colts-steelers','home',-3,'W'),
]
G={}
for f in glob.glob('data/2026-nfl-wk*/*.json'):
    if f.endswith('-graph.html'): continue
    d=json.load(open(f)); G[(int(d['week'].split()[1]),d['game_id'].replace('-2026-wk%s'%d['week'].split()[1],''))]=d
rows=[]
for wk,who,bb,gid,side,line,rec in P:
    d=G[(wk,gid)];s=d['simulation'];fs=d['final_score']
    mh=s.get('injuryAdjustedMarginHome'); src='inj-adj sim'
    if mh is None: mh=s.get('eloMarginHome'); src='Elo only (no inj-adj)'
    mkt=s.get('marketHomeSpread'); lineassumed=False
    pt=s.get('projTotalAdjusted') or s.get('projTotal')
    if side in('over','under'):
        edge=(line-pt) if side=='under' else (pt-line)
        lean=s.get('totalLean'); formal=None if lean is None else (lean==side)
        res_calc=None
        tot=fs['home']+fs['away']; res_calc='W' if (tot<line if side=='under' else tot>line) else ('P' if tot==line else 'L')
        mside=None
    else:
        if line is None: line=(mkt if side=='home' else -mkt); lineassumed=True  # side-relative from run-time market
        mside=mh if side=='home' else -mh
        edge=mside+line
        lean=s.get('spreadLean'); formal=None if lean is None else (lean==side)
        mg=(fs['home']-fs['away']) if side=='home' else (fs['away']-fs['home'])
        res_calc='W' if mg+line>0 else ('P' if mg+line==0 else 'L')
    rows.append(dict(wk=wk,who=who,bb=bb,game=gid,side=side,line=line,line_assumed=lineassumed,model_margin_home=mh,model_src=src,
        proj_total=pt,edge=round(edge,1),agree=edge>0,agree_strong=edge>=2,formal_lean=lean,formal_agree=formal,result=rec,regrade=res_calc,
        final=f"{fs['away']}-{fs['home']} (away-home)"))
json.dump(rows,open('results/wagertalk/wagertalk_vs_model.json','w'),indent=1)
with open('results/wagertalk/wagertalk_vs_model.csv','w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
def rec(rs): 
    w=sum(r['result']=='W' for r in rs);l=sum(r['result']=='L' for r in rs);return f"{w}-{l}"+(f" ({w/(w+l):.0%})" if w+l else "")
print('regrade mismatches:',[(r['wk'],r['game'],r['result'],r['regrade']) for r in rows if r['result']!=r['regrade']])
print('ALL',rec(rows))
for name,fn in [('agree(edge>0)',lambda r:r['agree']),('disagree',lambda r:not r['agree']),('strong agree(>=2)',lambda r:r['agree_strong']),('formal lean agree',lambda r:r['formal_agree'] is True),('formal lean opposes',lambda r:r['formal_agree'] is False),('no formal lean',lambda r:r['formal_agree'] is None)]:
    print(name,len([r for r in rows if fn(r)]),rec([r for r in rows if fn(r)]))
for tag,sub in [('wk1-2 (Elo only)',[r for r in rows if r['wk']<=2]),('wk3-5 (inj-adj)',[r for r in rows if r['wk']>=3])]:
    a=[r for r in sub if r['agree']];dd=[r for r in sub if not r['agree']]
    print(tag,'agree',rec(a),'disagree',rec(dd))
for k in('Marco','VR','Kelly'):
    a=[r for r in rows if r['who']==k and r['agree']];dd=[r for r in rows if r['who']==k and not r['agree']]
    print(k,'agree',rec(a),'disagree',rec(dd))
for b in (1,0):
    a=[r for r in rows if r['bb']==b and r['agree']];dd=[r for r in rows if r['bb']==b and not r['agree']]
    print('BB' if b else 'nonBB','agree',rec(a),'disagree',rec(dd))
print('assumed-line rows',[ (r['wk'],r['game']) for r in rows if r['line_assumed']])
print()
for r in rows: print(r['wk'],r['who'],r['game'],r['side'],r['line'],'edge',r['edge'],'A' if r['agree'] else 'D',r['formal_lean'],r['result'])

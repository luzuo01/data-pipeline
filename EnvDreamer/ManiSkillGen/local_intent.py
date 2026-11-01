"""Resolve explicitly described primitive identities in the local fallback parser."""
import re

COLORS = {'red':[.85,.1,.1,1.], 'blue':[.1,.2,.9,1.], 'green':[.1,.75,.2,1.], 'yellow':[.95,.75,.05,1.], 'white':[.9,.9,.9,1.]}
ALIASES = {'红':'red','蓝':'blue','绿':'green','黄':'yellow','白':'white', **{k:k for k in COLORS}}
COLOR = '('+'|'.join(ALIASES)+')'
DESCRIPTION = re.compile(COLOR+r'(?:色)?\s*(?:(small|large|小|大)\s*)?(?:cube|box|方块)', re.I)

def primitive_roles(text):
    descriptions=[]
    for match in DESCRIPTION.finditer(text):
        value=(ALIASES[match[1].lower()],match[2] or '')
        if value not in descriptions: descriptions.append(value)
    target=re.search(COLOR+r'(?:色)?\s*(?:target\s*(?:region)?|目标区域|区域)',text,re.I)
    moving=re.search(r'(?:pick\s+up|move|stack|把)\s*(?:only\s+)?(?:the\s+)?'+COLOR+r'(?:色)?\s*(?:small|large|小|大)?\s*(?:cube|box|方块)',text,re.I)
    return descriptions, ALIASES[target[1].lower()] if target else None, ALIASES[moving[1].lower()] if moving else None

def reject_unsupported_request(prompt):
    if re.search(r'倒水|倒入|杯中|毛巾|擦干|pour|towel|liquid',prompt,re.I):
        raise ValueError('不支持该任务：当前仅支持桌面几何物体的抓取放置、堆叠和推动，不支持杯碗液体倾倒或毛巾擦拭；未生成替代任务。')

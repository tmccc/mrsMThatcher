"""Generate and assess scene-grammar variants for difficult image cases."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import requests

from .bakeoff import FREE_TRADE_KEY
from .first_impression import EVEREST_QUOTE_HASH
from .generation_prompt_pilot import ImageClient, utc_now
from .io import atomic_write_json, sha256_file

PROMPT_VERSION="scene-grammar-v1"
STYLES=("scene_grammar_direct","scene_grammar_cinematic")
MAX_IMAGES=40;HARD_CEILING_USD=15.0
FAILURE_CATEGORIES={"scene too generic","wrong dominant subject","missing required object","incorrect physical relationship","poor scale","weak composition","wrong camera/viewpoint","tone mismatch","distracting symbolism","identity/likeness problem","visual quality problem","other"}
SCENE_FIELDS=("primary_subject","secondary_subjects","setting","camera_view","composition","scale_relationships","attention_hierarchy","must_include","must_avoid","forbidden_dominant_messages","desired_first_second_message","desired_tone","lighting","timeline_readability")

# These are physical scene recipes, not editorial labels. They replace the
# generic subject lists in Pilot 001 with one testable composition per quote.
SCENE_RECIPES: dict[str, dict[str, Any]] = {
    "8312afd41d8a4871f7c62f21e6bab0840e48aef15d48e311fc152cc1e1d03e1d": {
        "primary_subject": "a decorated British war veteran speaking calmly at Speakers' Corner while an opponent listens",
        "setting": "Speakers' Corner in Hyde Park, London, with recognisable British park railings and restrained period detail",
        "camera_view": "eye-level medium shot placing the veteran and listening opponent in the same focal plane",
        "composition": "one open public discussion; the veteran speaks while an opponent visibly allows him the floor; no protest montage",
        "scale_relationships": ["both speakers remain human-scale equals", "service medals are legible but do not dominate", "the listening gesture is visible at timeline size"],
        "attention_hierarchy": ["veteran exercising free speech", "opponent listening rather than silencing", "British public setting"],
        "must_include": ["British war veteran with restrained service medals", "an opponent listening", "Speakers' Corner setting"],
        "must_avoid": ["immigration placards", "angry riot", "gagged mouth", "generic censorship symbol"],
    },
    "3ba6cfba4bbf0673ce1cf563fb9de4821aded8422c64cbcd4218bf8da951f24d": {
        "primary_subject": "ordinary British factory workers locked outside an idle works because a monopoly union has halted the plant",
        "setting": "a British industrial town factory gate during an otherwise normal working day",
        "camera_view": "medium-wide eye-level view from the workers' side of the locked gate",
        "composition": "workers and idle machinery are connected in one scene; one union notice is small and secondary",
        "scale_relationships": ["affected workers dominate the frame", "the locked gate visibly separates them from idle work", "union signage remains evidence, not a heroic focal point"],
        "attention_hierarchy": ["workers unable to work", "locked idle factory", "small union stoppage notice"],
        "must_include": ["British workers", "locked factory gate", "idle production line visible beyond"],
        "must_avoid": ["heroic picket line", "violent clash", "closed factory with no causal evidence"],
    },
    "1100f7567ba493ed9044f6ba234bcbec3d99c9918bfc0ca33cd03ed56d44689a": {
        "primary_subject": "a busy owner-operated British high-street workshop serving real customers",
        "setting": "a recognisable British high street with a small workshop open for business",
        "camera_view": "street-level medium-wide view through the open workshop frontage",
        "composition": "the owner, productive work and customers form one clear scene; a distant municipal office is visually subordinate",
        "scale_relationships": ["productive private activity fills most of the frame", "customers and owner are clearly interacting", "government architecture occupies less than 15% of attention"],
        "attention_hierarchy": ["small business producing and selling", "customers choosing to buy", "British high street"],
        "must_include": ["working British small business", "owner producing something tangible", "paying customers"],
        "must_avoid": ["split-screen comparison", "stock exchange", "luxury finance", "government building as dominant subject"],
    },
    "5ff1169a7688823bd091284eaafe66348fc0d2fde67146ff2b20738b14a6663c": {
        "primary_subject": "a highly realistic recognisable Margaret Thatcher choosing and paying for an ordinary purchase with her own purse",
        "setting": "a modest independent shop on a recognisable British high street",
        "camera_view": "natural eye-level medium shot of the voluntary choice and payment",
        "composition": "Thatcher considers two practical goods and freely pays the shopkeeper; no crowd or podium",
        "scale_relationships": ["Thatcher and the act of choosing share the focal point", "money and selected item are visible but natural", "shop details remain secondary"],
        "attention_hierarchy": ["Margaret Thatcher making her own choice", "voluntary payment", "ordinary British shop"],
        "must_include": ["recognisable Margaret Thatcher", "genuine choice between ordinary goods", "payment from her own purse"],
        "must_avoid": ["political rally", "luxury shopping", "broken chains", "abstract liberty symbols"],
    },
    "5f343702a183a99143ff0935165238b5d5ee0732521a258470450e207fb147e5": {
        "primary_subject": "a British police community officer explaining a simple civic rule to attentive residents",
        "setting": "a town-hall meeting in a recognisable British community centre",
        "camera_view": "eye-level medium-wide view showing officer, residents and the rule being explained",
        "composition": "one calm act of public instruction; residents listen without coercion or confrontation",
        "scale_relationships": ["officer and residents occupy equal civic space", "the explanatory document is readable as an object but contains no rendered text", "police equipment is not prominent"],
        "attention_hierarchy": ["calm public instruction", "residents listening", "British civic setting"],
        "must_include": ["British community officer", "attentive residents", "calm explanation of lawful conduct"],
        "must_avoid": ["arrest", "riot gear", "courtroom punishment", "US police imagery"],
    },
    "123ba82403b7a5b6a3d6fe961997c01b1a3498e37a39e328cab098f297c26417": {
        "primary_subject": "ordinary British employee-shareholders actively running a formerly state-owned local workshop",
        "setting": "a productive British workshop recently transferred into private ownership",
        "camera_view": "eye-level medium-wide interior view centred on workers making decisions and serving a customer",
        "composition": "workers hold keys and ownership papers while continuing real work; the former state office sign is small and being removed",
        "scale_relationships": ["empowered workers dominate", "productive machinery and customer remain clear", "state symbol is small and receding"],
        "attention_hierarchy": ["workers exercising ownership", "productive enterprise", "receding state control"],
        "must_include": ["British workers with ownership responsibility", "active productive business", "subtle transfer from state control"],
        "must_avoid": ["stock-market montage", "giant government building", "abstract shrinking arrows", "luxury investors"],
    },
    "8f0426b8fbf61c24065fb2a216db165f9c03f343a3b603c011e6cef81dbbce28": {
        "primary_subject": "Western diplomats sitting inactive in a secure conference room while a European city burns visibly beyond the windows",
        "setting": "a Western European diplomatic conference room overlooking unmistakable nearby civilian destruction",
        "camera_view": "wide interior view from behind the empty action folder toward inactive officials and the crisis outside",
        "composition": "inaction and preventable suffering coexist in one physical view; no map, montage or battlefield spectacle",
        "scale_relationships": ["inactive officials and visible destruction receive comparable visual weight", "the untouched action folder sits in the foreground", "military hardware does not dominate"],
        "attention_hierarchy": ["official inaction", "civilian destruction nearby", "unused capacity to act"],
        "must_include": ["inactive Western officials", "nearby European civilian destruction", "clear unused capacity for action"],
        "must_avoid": ["mass graves", "graphic bodies", "map of Europe", "heroic combat", "named modern conflict"],
    },
    "a215345a00a2314d7d8e44dfaaf0e35c72821efe79ef85c0afef7da7a81ea34a": {
        "primary_subject": "British neighbours voluntarily helping a family repair damage after a personal mistake",
        "setting": "a recognisable British residential street after a minor household mishap",
        "camera_view": "warm eye-level medium-wide view showing choice, responsibility and voluntary help together",
        "composition": "the person who made the mistake works alongside compassionate neighbours; no official agency directs them",
        "scale_relationships": ["people and their voluntary action dominate", "the repair visibly follows the mistake", "government presence is absent rather than symbolically attacked"],
        "attention_hierarchy": ["personal responsibility", "voluntary compassion", "repaired consequence"],
        "must_include": ["person correcting a mistake", "neighbours helping voluntarily", "British domestic setting"],
        "must_avoid": ["charity poster", "government office", "helpless passive recipient", "ideological symbols"],
    },
    "0f3c7da5b7971b89c68fb0a5a07b1b23c563c32a94ee6b3ae5a77fce11d95bcc": {
        "primary_subject": "a highly realistic recognisable Margaret Thatcher finishing difficult work alone after colleagues have left",
        "setting": "a British government office late at night with empty desks and coats gone",
        "camera_view": "intimate eye-level medium shot across the final unfinished papers toward Thatcher",
        "composition": "Thatcher completes the visibly abandoned task; empty workstations prove others have left",
        "scale_relationships": ["Thatcher and unfinished work dominate", "empty desks establish abandonment without becoming eerie", "no symbolic object competes with her action"],
        "attention_hierarchy": ["Margaret Thatcher persisting", "unfinished work being completed", "empty office"],
        "must_include": ["recognisable Margaret Thatcher", "unfinished work being completed", "empty British workplace showing others have left"],
        "must_avoid": ["generic anonymous woman", "domestic chores", "heroic podium portrait", "crowded office"],
    },
    "ab81f26fbbdea5b50b5f95a4b89e91f2491d707ee3d98b06e1b2a8f203e5e659": {
        "primary_subject": "British citizens freely entering a courthouse while carefully respecting an ordinary lawful boundary",
        "setting": "an active British civic square outside a recognisable courthouse",
        "camera_view": "eye-level wide view connecting free public life with voluntary law-abiding conduct",
        "composition": "people move freely through the square and calmly observe one modest legal boundary; no police coercion",
        "scale_relationships": ["free citizens dominate", "courthouse anchors the background", "the lawful boundary is clear but not oppressive"],
        "attention_hierarchy": ["free law-abiding citizens", "British courthouse", "orderly shared space"],
        "must_include": ["British citizens acting freely", "visible respect for law", "recognisable British courthouse"],
        "must_avoid": ["prison bars", "riot", "authoritarian police line", "US Supreme Court"],
    },
    "c0c20416dce3033550ca0fc32b1a21b5302986c4595379da090002c38f6aac57": {
        "primary_subject": "NATO civilian and military leaders studying a realistic defence map while a peace celebration remains distant outside",
        "setting": "a NATO planning room in Western Europe during a period of public optimism",
        "camera_view": "medium-wide view over a sober defence plan toward leaders and distant celebrations through a window",
        "composition": "realistic planning dominates; celebration is visible but secondary, embodying optimism restrained by preparedness",
        "scale_relationships": ["leaders and defence plan dominate", "distant celebration occupies less than one quarter", "NATO emblem is small contextual evidence"],
        "attention_hierarchy": ["continued realistic defence planning", "allied leaders", "distant public euphoria"],
        "must_include": ["NATO planning", "allied cooperation", "visible but secondary peace euphoria"],
        "must_avoid": ["NATO flag collage", "missile spectacle", "triumphal celebration as dominant subject", "EU symbolism"],
    },
    "0ece322a106d6443bef89c14e769b28e8a19db61bbefa53c298cb206c2031ffa": {
        "primary_subject": "British citizens entering the Palace of Westminster beneath inherited constitutional symbols",
        "setting": "the Palace of Westminster in London with British civic continuity visible in stone, ceremony and public access",
        "camera_view": "low but human-scale medium-wide view from the public approach toward Parliament",
        "composition": "citizens and Parliament form one continuous British source of authority; Brussels is absent rather than caricatured",
        "scale_relationships": ["citizens remain prominent in the foreground", "Parliament provides enduring context", "Union Flag is recognisable but not oversized"],
        "attention_hierarchy": ["British people and Parliament", "constitutional heritage", "Union Flag"],
        "must_include": ["British citizens", "Palace of Westminster", "recognisable Union Flag", "historic constitutional continuity"],
        "must_avoid": ["EU headquarters", "Brexit rally", "Brussels skyline", "broken chains", "aggressive nationalism"],
    },
    "90a6e7991136e4ca46e618b5ff3cc570de1d16f1f47f69bfdc11e7004b44ee1d": {
        "primary_subject": "a highly realistic recognisable Margaret Thatcher responding with amused firmness during a British television interview",
        "setting": "a restrained British television interview studio",
        "camera_view": "tight medium shot capturing Thatcher's expressive dismissive gesture and wry expression",
        "composition": "Thatcher alone carries the rebuttal through expression and gesture; no metaphorical props",
        "scale_relationships": ["Thatcher's face and gesture dominate", "interviewer remains only a shoulder at frame edge", "studio background is quiet"],
        "attention_hierarchy": ["Margaret Thatcher's amused dismissal", "firm hand gesture", "interview context"],
        "must_include": ["recognisable Margaret Thatcher", "wry amused expression", "firm dismissive hand gesture"],
        "must_avoid": ["bending metal", "dictionary", "written word poppycock", "angry shouting", "cartoon style"],
    },
    "19b4b12f040bed61c1804b573327e36f4cc485636d799602a476e0b89fc8e8b1": {
        "primary_subject": "a gifted young British engineer demonstrating an invention while teachers, investors and workers enable it to grow",
        "setting": "an open British technical college workshop connected to a thriving local enterprise",
        "camera_view": "eye-level medium-wide view connecting the individual invention to people putting it into use",
        "composition": "one distinctive individual contribution visibly spreads into wider social and economic activity",
        "scale_relationships": ["the individual and invention dominate", "supporters visibly enable rather than direct", "wider flourishing remains concrete and secondary"],
        "attention_hierarchy": ["distinctive individual talent", "enabling institutions", "wider productive benefit"],
        "must_include": ["individual creative achievement", "people enabling rather than suppressing it", "visible wider benefit"],
        "must_avoid": ["diversity montage", "generic smiling office", "crushed person metaphor", "growth chart"],
    },
    "7767b08facbc57e7451fc13a335bca97a41b8aa3e694b68bc0388eff23424b4d": {
        "primary_subject": "a British politician promising overflowing consumption while a visibly smaller factory output sits behind the crowd",
        "setting": "a British town meeting beside a working factory and local shop",
        "camera_view": "medium-wide view aligning politician, eager consumers and insufficient real production in depth",
        "composition": "promised goods visibly exceed the smaller quantity being produced; the causal imbalance is physical, not a diagram",
        "scale_relationships": ["promised consumption pile is visibly much larger than actual output", "politician and claim remain prominent", "factory output is unmistakably limited"],
        "attention_hierarchy": ["promise of excess consumption", "insufficient real production", "misled public"],
        "must_include": ["British politician making promises", "consumption visibly exceeding production", "real factory output"],
        "must_avoid": ["price chart", "shopping-only scene", "money printing press", "named politician"],
    },
    "21db3d129f143edca731ac38704b1add8ef666662555fbbd3a91bd217d09f6a7": {
        "primary_subject": "an independent British market trader prevented from making a voluntary sale by a uniform state allocation officer",
        "setting": "a recognisable British covered market where one ordinary exchange is being blocked",
        "camera_view": "eye-level medium shot of buyer, seller and intervening allocation officer",
        "composition": "the incompatible actions occupy one scene: voluntary exchange begins, central direction physically stops it",
        "scale_relationships": ["buyer and seller are the intended focal action", "the official interruption is equally legible but not monstrous", "no ideology emblem dominates"],
        "attention_hierarchy": ["blocked voluntary exchange", "buyer and seller", "central direction"],
        "must_include": ["British market", "voluntary buyer and seller", "state direction preventing their exchange"],
        "must_avoid": ["hammer and sickle", "capitalism-versus-socialism poster", "broken chains", "split screen"],
    },
    "cb5c6ba02224a3ab087546b1b1429c8f8b32d452a8f2f8e2eb5e9595823d0120": {
        "primary_subject": "a British craft worker reduced to waiting for permission at an enormous state allocation counter while productive tools sit idle",
        "setting": "a drab British state office attached to an idle local workshop",
        "camera_view": "medium-wide view from beside the worker's idle tools toward the oversized official counter",
        "composition": "the diminished individual, enlarged state and drained productive wealth are one causal physical scene",
        "scale_relationships": ["state counter towers over the worker", "idle tools and empty order book remain visible", "no abstract giant statue or logo"],
        "attention_hierarchy": ["individual diminished by state control", "idle productive capacity", "oversized bureaucracy"],
        "must_include": ["British worker", "state permission barrier", "idle productive tools", "visible economic depletion"],
        "must_avoid": ["communist symbols", "generic poverty queue", "capitalist hero", "currency draining illustration"],
    },
    "1fd9dc8bc79b72ba11ce420cab2061aa9586117e10f8c38f6f60ab3c2a55b739": {
        "primary_subject": "a British minister answering difficult questions directly before elected MPs and watching constituents",
        "setting": "the House of Commons chamber during accountable ministerial questioning",
        "camera_view": "eye-level medium-wide view connecting minister, MPs and public gallery",
        "composition": "the accountable exchange dominates; a small stack of unattended bureaucratic forms is peripheral",
        "scale_relationships": ["minister and questioning MPs dominate", "constituents are clearly visible in the gallery", "bureaucratic paperwork occupies less than 10%"],
        "attention_hierarchy": ["ministerial accountability", "elected representatives", "watching electorate"],
        "must_include": ["British minister answering Parliament", "elected MPs questioning", "public gallery"],
        "must_avoid": ["empty chamber", "generic office bureaucracy", "ballot-box montage", "party campaign branding"],
    },
}


def classify_failures(manifest:dict[str,Any],reviews:dict[str,Any],generated:dict[str,Any],analyses:dict[str,Any])->dict[str,Any]:
    """Classify failures."""
    rows=[]
    for case in manifest["items"]:
        review=reviews["items"][case["case_id"]]
        if review["preferred_candidate"]!="none":continue
        note=review.get("notes","").lower();texts=[]
        for cid,row in generated["items"].items():
            if row["case_id"]==case["case_id"]:texts.append((analyses["items"][cid]["first_impression_alignment"].get("dominant_mismatch") or "").lower())
        joined=" ".join(texts);cats=[]
        if "uk" in note or "flag" in note:cats.append("missing required object")
        if case["quote_hash"]==EVEREST_QUOTE_HASH:cats += ["incorrect physical relationship","poor scale"]
        if "thatcher" in note:cats.append("identity/likeness problem")
        if any(x in joined for x in ("generic","general","lacks any","omits any","market","community montage")):cats.append("scene too generic")
        if any(x in joined for x in ("dominant first impression","leads with","centers","heroized")):cats.append("wrong dominant subject")
        if any(x in joined for x in ("tone","triumphant","celebratory","euphoria","mournful")):cats.append("tone mismatch")
        if any(x in joined for x in ("collage","split","logo","symbol","poster")):cats.append("distracting symbolism")
        if not cats:cats=["weak composition"]
        cats=list(dict.fromkeys(cats));rows.append({"case_id":case["case_id"],"quote_hash":case["quote_hash"],"categories":cats,"tony_note":review.get("notes","")})
    return {"schema_version":1,"analysis_kind":"offline_scene_failure_analysis","items":rows,"category_counts":{c:sum(c in x["categories"] for x in rows) for c in sorted(FAILURE_CATEGORIES)}}


def scene_spec(case:dict[str,Any],brief:dict[str,Any],failure:dict[str,Any]|None=None)->dict[str,Any]:
    """Return the scene spec."""
    subjects=list(brief.get("must_include") or [])
    primary=brief.get("desired_primary_subject") or (subjects[0] if subjects else "a concrete human action expressing the quotation")
    quote=case["quote_text"].lower();domestic=any(x in quote for x in ("britain","nation","law","brussels","government","state","society")) or "uk" in (failure or {}).get("tony_note","").lower()
    spec={"schema_version":1,"prompt_version":PROMPT_VERSION,"case_id":case["case_id"],"quote_hash":case["quote_hash"],
        "primary_subject":primary,"secondary_subjects":subjects[1:3],"setting":"a recognisably British real-world setting" if domestic else "a specific real-world setting appropriate to the described action",
        "camera_view":"eye-level medium-wide editorial photograph with one clear focal plane","composition":"single coherent scene; primary subject centered off-axis with unobstructed silhouette and no montage, split screen, poster, diagram or caption",
        "scale_relationships":["primary subject occupies 40-60% of the visual attention","secondary subjects remain visibly subordinate","no emblem or background object is larger or higher contrast than the primary action"],
        "attention_hierarchy":[primary,*subjects[1:3]],"must_include":subjects[:3],"must_avoid":list(brief.get("must_avoid") or []),"forbidden_dominant_messages":list(brief.get("forbidden_dominant_messages") or []),
        "desired_first_second_message":brief["desired_first_impression"],"desired_tone":brief.get("desired_tone") or [],"lighting":"naturalistic directional light supporting the requested tone without theatrical glow","timeline_readability":"high","failure_corrections":list((failure or {}).get("categories",[])),"provenance":"deterministic scene grammar compiled from cached brief and offline human-note failure classification"}
    if case["quote_hash"] in SCENE_RECIPES:
        recipe=SCENE_RECIPES[case["quote_hash"]]
        spec.update(recipe)
        spec["secondary_subjects"]=recipe["must_include"][1:3]
        spec["must_avoid"]=list(dict.fromkeys([*recipe["must_avoid"], *spec["must_avoid"]]))[:12]
    if case["quote_hash"]==EVEREST_QUOTE_HASH:
        spec.update(primary_subject="a lone climber standing on the unmistakable highest summit of Mount Everest and planting a clearly recognisable Union Flag",secondary_subjects=["lower Himalayan peaks far below","cloud layer below the summit"],setting="the summit ridge of Mount Everest",
            camera_view="low three-quarter wide view from just below the summit, showing the climber and full height dominance of Everest",composition="single photorealistic summit scene; Everest and the Union Flag dominate; no expedition camp or prayer flags in the foreground",
            scale_relationships=["Everest summit is visibly taller than every surrounding peak","all surrounding peaks sit clearly below the summit horizon","Union Flag is large enough to identify at timeline size but smaller than the mountain and climber"],attention_hierarchy=["Everest summit and climber","Union Flag","lower surrounding peaks"],must_include=["recognisable Union Flag","climber at the summit","surrounding peaks visibly lower"],must_avoid=["US flag","Dutch flag","prayer flags","base camp","communist symbols","maps","geopolitical imagery"],forbidden_dominant_messages=["foreign expedition nationalism","communist warning","geopolitical conflict"])
    elif case["quote_hash"]==FREE_TRADE_KEY[0]:
        spec.update(primary_subject="a British small manufacturer completing a voluntary export sale with an overseas buyer",secondary_subjects=["finished British goods being loaded for export","buyer confirming payment and receipt"],setting="a recognisably British small factory loading bay connected to an international port",
            camera_view="eye-level medium-wide view close enough to read the handshake, goods and exchange",composition="single realistic transaction scene; seller, buyer and exchanged goods form one visual triangle; port activity remains secondary",
            scale_relationships=["the voluntary exchange between seller and buyer is the dominant action","goods and payment evidence are clearly visible but subordinate to the people","no political symbol dominates the transaction"],attention_hierarchy=["voluntary exchange","British-made goods","international shipping context"],must_include=["British small business","buyer and seller freely agreeing","real goods moving in exchange","subtle recognisable UK setting"],must_avoid=["hammer and sickle","split capitalism-versus-socialism poster","generic stock-market imagery","luxury-only prosperity"],forbidden_dominant_messages=["ideological conflict","communist threat","abstract finance","government trade negotiation"])
    return spec


def validate_spec(spec:dict[str,Any])->dict[str,Any]:
    """Validate spec."""
    missing=[x for x in SCENE_FIELDS if x not in spec]
    if missing:raise ValueError(f"missing scene fields: {missing}")
    if not spec["primary_subject"] or not spec["camera_view"] or not spec["composition"] or not spec["scale_relationships"] or not spec["must_include"]:raise ValueError("scene grammar lacks concrete physical controls")
    return spec


def compile_prompt(spec:dict[str,Any],style:str)->str:
    """Compile prompt."""
    validate_spec(spec)
    if style not in STYLES:raise ValueError("unknown scene style")
    render={"scene_grammar_direct":"naturalistic documentary editorial photography, factual and restrained","scene_grammar_cinematic":"cinematic photorealism with stronger depth, atmosphere and lighting while preserving every physical fact"}[style]
    semantic={k:spec[k] for k in SCENE_FIELDS}
    return f"Prompt version: {PROMPT_VERSION}. Rendering mode: {render}.\nCreate one square editorial photograph for a quotation displayed separately. The following scene specification is mandatory and literal about objects, scale, location, viewpoint and attention. Do not add text, captions, watermarks, diagrams, collages, split screens or generic political-poster symbolism. The first item in attention_hierarchy must be what a scrolling viewer notices first.\nSCENE_SPECIFICATION:{json.dumps(semantic,sort_keys=True,ensure_ascii=False)}"


def candidate_id(case_id:str,style:str)->str:
    """Return a stable candidate identifier."""
    return hashlib.sha256(f"{case_id}:{style}:{PROMPT_VERSION}".encode()).hexdigest()[:20]


def blinded_candidates(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return the stable reviewer projection without style or prompt provenance."""
    ordered = sorted(rows, key=lambda row: row["candidate_id"])
    return [
        {
            "label": "AB"[index],
            "candidate_id": row["candidate_id"],
            "image_basename": row["image_basename"],
        }
        for index, row in enumerate(ordered)
    ]


def preflight(cases:int)->dict[str,Any]:
    """Build the deterministic execution preflight."""
    calls=cases*2;generation=calls*.013
    identity_calls=6 if cases==20 else 0
    analysis=calls*((2.536823+.291034)/50)+identity_calls*(.291034/50)
    maximum=2*(generation+analysis)
    return {"schema_version":1,"cases":cases,"images":calls,"identity_audit_calls":identity_calls,"expected_generation_cost_usd":round(generation,4),"expected_analysis_cost_usd":round(analysis,4),"expected_total_cost_usd":round(generation+analysis,4),"conservative_retry_total_usd":round(maximum,4),"hard_ceiling_usd":HARD_CEILING_USD,"allowed":cases==20 and calls<=MAX_IMAGES and maximum<=HARD_CEILING_USD,"provider_trial":"deferred to avoid confounding scene grammar with provider"}


def execute(run:Path,manifest:dict[str,Any],specs:dict[str,Any],client:ImageClient,limit:float,sleep=time.sleep)->dict[str,Any]:
    """Return the execute."""
    if limit!=HARD_CEILING_USD or not preflight(len(manifest["items"]))["allowed"]:raise RuntimeError("exact $15 ceiling and passing preflight required")
    path=run/"generated_candidates.json";db=json.loads(path.read_text()) if path.exists() else {"schema_version":1,"items":{},"attempts":[]}
    for case in manifest["items"]:
        for style in STYLES:
            cid=candidate_id(case["case_id"],style)
            if cid in db["items"]:continue
            prompt=compile_prompt(specs[case["case_id"]],style)
            for attempt in range(1,3):
                rec={"candidate_id":cid,"case_id":case["case_id"],"quote_hash":case["quote_hash"],"style":style,"attempt":attempt,"state":"sending","prepared_at":utc_now()};db["attempts"].append(rec);atomic_write_json(path,db)
                try:
                    image,meta=client.generate(prompt);image_path=run/"images"/f"{cid}.png";image_path.parent.mkdir(parents=True,exist_ok=True);tmp=image_path.with_suffix(".tmp");tmp.write_bytes(image);tmp.replace(image_path);rec["state"]="completed";db["items"][cid]={"candidate_id":cid,"case_id":case["case_id"],"quote_hash":case["quote_hash"],"style":style,"image_basename":image_path.name,"path":str(image_path),"sha256":sha256_file(image_path),"response_metadata":meta,"generated_at":utc_now()};atomic_write_json(path,db);break
                except requests.RequestException as exc:
                    rec["state"]="confirmed_failure";rec["error"]=str(exc);atomic_write_json(path,db)
                    if attempt==2:break
                    sleep(2**attempt)
    return db

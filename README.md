# eBay New Drops — watcher + instructions

Ye repo do eBay storefronts ko watch karta hai aur naye listings Telegram par
bhejta hai. Ye purane `ebay-store-watcher` ka **alag, naya** setup hai: apna bot,
apna schedule, apni memory. Dono ek doosre se bilkul independent chalte hain.

Setup ki date: **7 September 2026** — ye purane watcher ka naya, alag repo hai.

---

## 1. Ye karta kya hai

Do eBay storefronts ko watch karta hai aur **sirf naye listed products** aapke
Telegram DM me bhejta hai — photo album ke saath aur clickable link ke saath.
GitHub Actions par apne aap chalta hai, aapka laptop band ho tab bhi.

| Cheez | Value |
|---|---|
| Repo | `equalifieds/ebay-new-drops` (**public**) |
| Telegram bot | `@eBay_New_Drops_bot` |
| Chat id | aapka apna Telegram DM — repo secret `TELEGRAM_CHAT_ID` me, file me kabhi nahi |
| Schedule | har **2 ghante** — cron `45 */2 * * *` (UTC) = 06:15, 08:15, 10:15 … IST |
| Stores | HerbalDirect, VitaminRush Health Shop (`stores.json` me) |

---

## 2. Files

| File | Kaam |
|---|---|
| `scrape.py` | Main script — scrape karta hai, purane se compare karta hai, Telegram bhejta hai |
| `parser.py` | Sirf HTML → product parsing. Koi network nahi, isliye test karna aasan |
| `stores.json` | Kaun se stores watch karne hain |
| `.github/workflows/watch.yml` | Schedule, dependencies, run, snapshot wapas commit karna |
| `requirements.txt` | Python packages |
| `tests/` | 260 offline tests. Internet ki zarurat nahi |
| `data/<slug>.json` | **Pehle run par khud banta hai** — har store ki memory. Repo me commit mat kijiye |
| `HANDOFF.md` | Doosre Claude/developer ko dene ke liye technical summary |
| `TEST-REPORT.md` | QA report — kaun se 19 bugs mile aur fix hue |

> `data/` folder shuru me nahi hota — pehla run use khud bana leta hai. Ye live
> memory hai jo har run me GitHub par update hoti rehti hai. Kisi purane backup
> se `data/` **kabhi mat** copy kijiye — warna purani memory laut aayegi aur
> naye products chhoot jayenge.

---

## 3. Ye kaam kaise karta hai — sabse zaroori baat

Har store ki snapshot file me ek **`seen`** list hoti hai: us store par **ab tak
dekhe gaye har item ka id**. Alert sirf tab jaata hai jab koi id `seen` me nahi
hai. `seen` kabhi chhoti nahi hoti, sirf badhti hai.

Ye decoration nahi hai. eBay pagination ke beech listings ko shuffle karta rehta
hai, isliye ek product ek scrape me gayab hoke agle me wapas aa jaata hai. Sirf
"is run vs pichhle run" compare karne se ek raat me **449 jhooth-mooth ke alerts**
aaye the. `seen` ke saath: 9,000 items ka store, poora page re-shuffle, har run me
300 items gayab — 5 runs me **0** false alerts.

**Agar kabhi alerts ki baadh aa jaye:** GitHub → Actions → "eBay store watcher" →
Run workflow → **`reset` tick karke** run kar dijiye. Ye `seen` ko sab kuch se
bhar dega aur ek bhi product alert nahi bhejega.

**Pehla run bhi yahi karta hai** — baseline banata hai, kuch bhejta nahi. Ye
normal hai, ghabraiye mat.

---

## 4. Telegram bot aur secrets

Is repo ka apna alag bot hai: **`@eBay_New_Drops_bot`** (purane
`@wdbEbayStoreWatcherBot` se alag, taaki dono watchers ke alerts na mix hon).

### Pehli baar bot set karna

1. Telegram me **@BotFather** kholiye
2. `/newbot` bhejiye
3. Display name: `eBay New Drops`
4. Username: `eBay_New_Drops_bot`  ← ye already bana hua hai
5. BotFather ek **token** dega — `1234567890:AA...` jaisa. **Use kisi file me
   mat likhiye, kisi chat me paste mat kijiye.** Seedha repo secret me daaliye.
6. Ab apne naye bot ko kholiye aur **`/start`** bhejiye. Ye zaroori hai — jab
   tak aap bot se baat shuru nahi karte, bot aapko message nahi bhej sakta.

### Chat id nikalna

Aapka chat id purane bot wala hi rahega (wo aapki Telegram user id hai, bot ki
nahi). Agar dobara chahiye ho: naye bot ko `/start` bhejne ke baad browser me
kholiye —
`https://api.telegram.org/bot<TOKEN>/getUpdates` — response me
`"chat":{"id":...}` hi aapka chat id hai.

### Repo secrets

Settings → Secrets and variables → Actions → **New repository secret**, do
secrets:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather wala token |
| `TELEGRAM_CHAT_ID` | aapka chat id |

**Ye kabhi bhi kisi file me mat likhiye.** Code me kahin hardcode nahi hai, aur
is repo me bhi nahi hai — jaan-boojh kar. Ye repo **public** hai, isliye ye baat
aur bhi zaroori hai. Agar token kahin leak ho jaye to BotFather me `/revoke`
karke naya bana lijiye aur repo secret update kar dijiye.

> **Public repo ka matlab:** code, `stores.json`, aur `data/*.json` snapshots
> sabko dikhte hain, aur Actions ke run logs bhi public hain. Secrets phir bhi
> chhupe rehte hain — GitHub unhe logs me `***` kar deta hai, aur code kabhi
> token print nahi karta. Agar aapko store list ya snapshots private chahiye,
> repo ko private kar dijiye — lekin phir cron wapas `45 */3 * * *` karna hoga
> (section 5 dekhiye).

---

## 5. Rozmarra ke kaam

### Naya store add karna
`stores.json` me ek entry jodiye:
```json
{ "slug": "somestore", "name": "Some Store", "url": "https://www.ebay.com/str/somestore" }
```
`slug` hi snapshot file ka naam banta hai — unique aur stable rakhiye. Pehle run
par uska baseline banega (koi alert nahi), uske baad se naye products aayenge.

### Schedule badalna
`.github/workflows/watch.yml` me cron line:
```
    - cron: "45 */2 * * *"
```
Cron **UTC** me hai. `45` do wajah se:

1. IST me runs quarter-past par aate hain (06:15, 08:15, 10:15 …)
2. Purana watcher repo `:30` par chalta hai. 15 minute ka offset rakhne se dono
   kabhi ek hi minute me eBay ko hit nahi karte.

> **Actions minutes:** ye repo **public** hai, aur public repos ko GitHub
> Actions ke **unlimited** free minutes milte hain. Isliye har 2 ghante wala
> schedule yahan bilkul safe hai.
>
> **Agar aap kabhi is repo ko private kar dein**, to pehle cron wapas
> `45 */3 * * *` kar dijiye. Private repo ko mahine ke sirf 2,000 free minutes
> milte hain, ek run ~5 minute leta hai:
> - har 3 ghante = 8 run/din ≈ **1,250 min/mahina** (safe)
> - har 2 ghante = 12 run/din ≈ **1,900 min/mahina** (95% — risky)
> - har 1 ghanta = 24 run/din ≈ **3,750 min/mahina** (limit paar, watcher beech
>   mahine me chup ho jayega)

### Haath se ek run chalana
GitHub → Actions → "eBay store watcher" → **Run workflow**.

### Bot ya chat badalna
Sirf repo secrets badaliye. Code chhune ki zarurat nahi.

---

## 6. Kuch gadbad ho to

| Dikh raha hai | Matlab | Kya karein |
|---|---|---|
| Actions run **red**, log me "surge" | Ek hi baar me 500+ naye items mile — shayad eBay ka markup badla | Log padhiye. Agar sach me restock hai to `reset` ke saath run kar dijiye |
| Telegram bilkul chup | Ya to koi naya product nahi hai (normal), ya secret galat hai | Actions ka latest log kholiye — wo saaf batata hai |
| Log me "blocked" | eBay ne rate-limit ya bot-check kiya | **Kuch bhi mat badliye.** Kuch ghante rukiye. Delay kam mat kijiye, parallel requests mat jodiye |
| Push reject / conflict | Do runs takra gaye | Workflow khud rebase karke 3 baar retry karta hai. Apne aap theek ho jaata hai |
| Ek hi product dobara aa gaya | Snapshot corrupt ho gaya tha | Ek baar hoke ruk jayega. `reset` ki zarurat nahi |

---

## 7. eBay par load — ise mat badhaiye

Har run me **51 GET requests** (HerbalDirect 3 + VitaminRush 48), yaani
**612 requests/din** 2-ghante wale schedule par (12 run × 51).

> 3-ghante wale schedule par ye 408/din tha. 2 ghante par load ~50% badh gaya
> hai. Yahi is repo me eBay par sabse bada badlaav hai — baaki sab waisa hi
> polite hai jaisa tha. Isse aur mat badhaiye.

Requests ek-ek karke jaati hain, beech me **2–4 second** ka gap hai. Koi
parallelism nahi, koi user-agent rotation nahi, koi bot-detection evasion nahi.

Aapko pehle ek crude scraper se eBay ka CAPTCHA mil chuka hai. Isliye:

- delay **kam mat kijiye**
- parallel requests **mat jodiye**
- schedule ko 1 ghante se neeche **mat laiye**
- agar eBay mana karne lage to **peeche hatiye**, uska raasta nikaalne ki koshish
  mat kijiye

`robots.txt` check kiya gaya hai — `/str/` pagination `User-agent: *` ke liye
allowed hai.

---

## 8. Laptop par chalana / test karna

```bash
pip install -r requirements.txt

python scrape.py --dry-run    # scrape + compare, kuch bheje bina
python scrape.py --reset      # baseline dobara banaye, koi alert nahi
python3 -m pytest tests/ -q   # 260 tests, poori tarah offline
```

Asli alert bhejne ke liye ye do environment variables chahiye:
```bash
export TELEGRAM_BOT_TOKEN=...      # apna token khud daaliye
export TELEGRAM_CHAT_ID=...              # apna chat id khud daaliye
```

Tests ka expected result: **256 pass, 1 xfail, 3 fail**. Wo 3 "fail" jaan-boojh
kar hain — unke naam hi bata dete hain (`..._are_not_rejected`,
`..._cause_a_permanent_repeating_flood`, `..._pass_validation_unstripped`). Wo
bugs ko pin karne ke liye likhe gaye the aur ab fail hote hain kyunki bugs fix
ho chuke hain.

---

## 9. Zero se repo khada karna

1. GitHub par naya **public** repo banaiye (`ebay-new-drops`)
2. Is folder ki sab files upload kar dijiye (`.github/workflows/watch.yml` ka
   path bilkul wahi rakhiye) — `data/` folder **mat** banaiye
3. Section 4 ke steps se bot banaiye aur dono secrets add kijiye
4. Actions tab kholiye → "I understand my workflows, go ahead and enable them"
5. Ek baar haath se "Run workflow" chalaiye — pehla run baseline banayega aur
   koi alert nahi bhejega (ye ~5 minute lega)
6. Agle scheduled run (06:15 / 08:15 / 10:15 … IST) se naye products aane
   lagenge

---

## 10. Abhi bhi khula hua (known issues)

- **Corrupt snapshot** us store ki poori `seen` history mita deta hai. Baadh nahi
  aati, lekin abhi eBay se gayab har id ek baar dobara announce ho jaati hai.
  Behtar hota last committed version se recover karna. Tests me `xfail` mark hai
  taaki bhoole na.
- **500+ ka asli restock Actions run ko red kar deta hai**, kyunki surge guard
  use failure gin leta hai. Snapshot phir bhi save ho jaata hai aur agla run
  shaant rehta hai — ye shor hai, kharabi nahi.
- `data/supplementhealthshoppe.json` ~2.3 MB ka hai aur din me 8 baar dobara
  likha jaata hai. Git history dheere-dheere badhegi. Aage chalke `seen` ko alag
  file me nikaalna theek rahega.

# Telegram Group Admin Bot

## Setup Steps

1. **Bot banao BotFather se:**
   - Telegram me @BotFather ko message karo
   - `/newbot` bhejo, naam aur username do
   - Jo token milega (kuch aisa: `123456789:ABCdefGhIJKlmNoPQRstuVWxyz`) usse copy kar lo

2. **Dependencies install karo:**
   ```
   pip install -r requirements.txt
   ```

3. **Tokens set karo** — do tarike hain:
   - Environment variables set karo:
     ```
     export TELEGRAM_BOT_TOKEN="apna_telegram_token_yaha"
     export GEMINI_API_KEY="apna_gemini_key_yaha"
     ```
   - YA `group_admin_bot.py` file me `BOT_TOKEN = "PUT_YOUR_BOT_TOKEN_HERE"` line me apna token daal do (Gemini key ke liye bhi wahi tarika)

   **Free Gemini API key kaise banaye:**
   - [aistudio.google.com/apikey](https://aistudio.google.com/apikey) pe jao
   - Google account se login karo
   - "Create API Key" dabao — bina card ke free key mil jayegi

4. **Bot ko group me add karo:**
   - Apne group me bot ko add karo
   - Bot ko **Admin** banao in permissions ke saath: Ban users, Delete messages, Pin messages, Add admins

5. **Bot run karo:**
   ```
   python group_admin_bot.py
   ```

## Commands

Zyada tar moderation commands ab **teen tarike** se target le sakte hain: reply karke, `@username` se, YA seedha unki **numeric user ID** se (jaise `/ban 123456789 spamming`) — ID wala tarika tab kaam aata hai jab user ka koi public username na ho.

| Command | Kya karta hai |
|---|---|
| `/kick` | User ko group se nikalta hai — dobara aa sakta hai |
| `/ban [reason]` | User ko permanently ban karta hai |
| `/unban <user_id or @username>` | User ko unban karta hai |
| `/mute [minutes]` | User ko mute karta hai — minutes na do to permanent |
| `/unmute` | User ko unmute karta hai |
| `/warn [reason]` | Warning deta hai, 3 warnings pe auto-ban |
| `/warnings` | User ki warning count dikhata hai |
| `/resetwarns` | Warnings reset karta hai |
| `/promote` | User ko admin banata hai |
| `/demote` | User ko admin se hatata hai |
| `/pin` | (reply karke) Message pin karta hai |
| `/unpin` | Pinned message hatata hai |
| `/purge` | (reply karke) Us message se ab tak sab delete karta hai |
| `/rules` | Group rules dikhata hai |
| `/setrules <text>` | Rules set karta hai |
| `/filters <trigger>` | Reply karke koi message/sticker ke saath — jo bhi koi trigger word bolega, bot wahi bhejega. Bina reply ke `/filters <trigger> <response text>` bhi chalega |
| `/delfilters <trigger>` | Auto-reply hataata hai |
| `/addblacklist <words>` | Reply karke koi message/sticker ke saath — wo group me bhejte hi auto-delete hoga. Bina reply ke `/addblacklist <word1> <word2> ...` bhi chalega. Aakhir me `{warn}` add karo to sender ko warning bhi milegi (jaise `/addblacklist bc {warn}`) |
| `/removeblock <words>` | Blocklist se words hataata hai (ya sticker pe reply karke hatao) |
| `/approve` | User ko blocklist se exempt karta hai (admins pehle se hi exempt hote hain) |
| `/unapprove` | User ki blocklist exemption hataata hai |
| `/setwelcome [text]` | Reply karke koi message/sticker ke saath welcome message set karo, ya `/setwelcome <text>` likho — `{name}` ya `{username}` se naye member ko mention karo |
| `/delsetwelcome` | Welcome message hataata hai |
| `/truth` | Random truth question deta hai |
| `/dare` | Random dare challenge deta hai |
| `/tr <language>` | (reply karke) Message translate karta hai |
| `/game` | Word chain game — lobby khulta hai, `/game` dobara bhejo start karne ke liye |
| `/join` | Open lobby me join karta hai (kam se kam 2 players chahiye) |
| `/endgame` | Game ko force-end karta hai (sirf admin) |
| `/info` | Apna ya kisi aur ka info dikhata hai |
| `/developer` | Bot ke developer (@liesworlds) ki details dikhata hai |
| `/ping` | Bot online hai ya nahi, aur response speed check karta hai |
| `/commands` | Sab commands ek jagah list karta hai |

**`/start`** ab buttons ke saath aata hai: **➕ Add me to your Group** (direct group me add karne ka link, admin permissions ke saath), **📋 All Commands**, aur **👨‍💻 Developer**.

**Bonus:** DM me koi bhi sticker bheje to bot wahi wapas bhejta hai. Group me sirf tabhi reply karega jab sticker **bot ke message pe reply** karke bheja gaya ho (users aapas me sticker bhejein to bot beech me nahi aata).

**Word Chain game:** Ab bot free Dictionary API (dictionaryapi.dev) se check karta hai ki bheja gaya word actual English word hai ya nahi — random letters accept nahi honge.

## Railway pe Deploy Karna

1. **GitHub repo banao** aur ye files (`group_admin_bot.py`, `requirements.txt`, `Procfile`) usme push kar do.

2. **Railway pe naya project banao:**
   - [railway.app](https://railway.app) pe login karo
   - "New Project" → "Deploy from GitHub repo" → apna repo select karo

3. **Environment variables set karo:**
   - Project ke "Variables" tab me jao
   - Add karo: `TELEGRAM_BOT_TOKEN` = `apna_bot_token`
   - Add karo: `GEMINI_API_KEY` = `apna_free_gemini_key` (aistudio.google.com/apikey se free milegi)
   - Add karo (optional): `DEVELOPER_CHAT_ID` = `apna_numeric_telegram_id` — isse set karne par, jab bhi koi user bot ko DM me `/start` karega, tumhe notification milegi (naye user ka naam + username + ID ke saath). Apni ID pata karne ke liye Telegram me @userinfobot ko message karo.
   - (Isliye script me hardcode nahi kiya token — env var se hi lega)

4. **Service type check karo:**
   - Railway "Procfile" dekh ke `worker` process chalayega (ye polling bot hai, "web" nahi — koi port listen nahi karta)
   - Agar Railway "web" process dhundhne ki koshish kare aur fail ho, to Settings me manually "Start Command" ko `python group_admin_bot.py` set kar do

5. **Deploy** — Railway automatically build aur run kar dega. Logs me "🤖 Bot chalu ho gaya..." dikhna chahiye.

⚠️ **Note:** Warnings, rules, filters, blocklist, welcome message, aur game state sab **memory me** store hote hain — Railway redeploy/restart (naya push, crash, ya sleep) hone par reset ho jayenge. Permanent chahiye to SQLite/PostgreSQL add karna hoga — bata dena.

**Reliability:** Bot me ab ek global error handler hai — koi bhi unexpected bug aaye to sirf wo ek action fail hoga, **pura bot crash nahi hoga**. Agar `DEVELOPER_CHAT_ID` set hai, to aisi koi bhi error tumhe DM me detail ke saath report ho jayegi (debugging ke liye).

## Important Notes

- Zyada tar moderation commands **reply ya `@username`** dono se target le sakte hain.
- Sirf group admins hi moderation commands chala sakte hain (live check hota hai Telegram API se).
- Bot khud bhi admin hona zaroori hai warna ban/mute/pin/promote kaam nahi karenge — group ke admin settings me bot ko "Add new admins" permission bhi dena zaroori hai `/promote` aur `/demote` ke liye.
- **@username se target karna:** Telegram ki limitation hai ki bot sirf un users ko `@username` se resolve kar sakta hai jinhone kabhi bot ya group me interact kiya ho. Agar `@username` kaam na kare, to reply-based tarika use karo (hamesha kaam karega).
- **Word Chain game** sirf **English** dictionary words accept karta hai (dictionaryapi.dev API se check hota hai). Agar API unreachable ho, to game bina check ke chalta rehta hai (fail-open).
- 24/7 chalane ke liye ise kisi server/VPS ya Railway/Render jaisi service pe host karna hoga.

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

Zyada tar moderation commands ab **do tarike** se target le sakte hain: reply karke, YA seedha `@username` command ke saath (jaise `/ban @someuser spamming`).

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
| `/filters <trigger> <response>` | Auto-reply add karta hai (koi keyword bole to bot reply karega) |
| `/delfilters <trigger>` | Auto-reply hataata hai |
| `/addblocklist <words>` | Un words wale messages auto-delete honge |
| `/removeblock <words>` | Blocklist se words hataata hai |
| `/setwelcome <text>` | Naye members ke liye welcome message set karta hai (`{name}` use karo) |
| `/delsetwelcome` | Welcome message hataata hai |
| `/truth` | Random truth question deta hai |
| `/dare` | Random dare challenge deta hai |
| `/tr <language>` | (reply karke) Message translate karta hai |
| `/game` | Word chain game — lobby khulta hai, `/game` dobara bhejo start karne ke liye |
| `/join` | Open lobby me join karta hai (kam se kam 2 players chahiye) |
| `/info` | Apna ya kisi aur ka info dikhata hai |
| `/developer` | Bot ke developer (@liesworlds) ki details dikhata hai |
| `/commands` | Sab commands ek jagah list karta hai |

**`/start`** ab buttons ke saath aata hai: **➕ Add me to your Group** (direct group me add karne ka link, admin permissions ke saath), **📋 All Commands**, aur **👨‍💻 Developer**.

**Bonus:** Koi bhi sticker bheje to bot wahi sticker wapas bhej deta hai.

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

⚠️ **Note:** Warnings aur rules memory me store hote hain, to jab bhi Railway redeploy/restart karega (naya push, crash, ya sleep), ye data reset ho jayega. Agar permanent chahiye to SQLite/PostgreSQL add karna hoga — bata dena, main add kar dunga.

## Important Notes

- Zyada tar commands **reply-based** hain — target user ke message pe reply karke command likho.
- Sirf group admins hi ye commands chala sakte hain (live check hota hai Telegram API se).
- Bot khud bhi admin hona zaroori hai warna ban/mute/pin kaam nahi karenge.
- **@username se target karna:** Telegram ki limitation hai ki bot sirf un users ko `@username` se resolve kar sakta hai jinhone kabhi bot ya group me interact kiya ho. Agar `@username` kaam na kare, to reply-based tarika use karo (hamesha kaam karega).
- Warnings aur rules abhi **memory me** store hote hain — bot restart hone pe reset ho jayenge. Agar permanent chahiye to database (SQLite/PostgreSQL) add karna padega — bata dena agar wo bhi chahiye.
- 24/7 chalane ke liye ise kisi server/VPS ya Railway/Render jaisi service pe host karna hoga.

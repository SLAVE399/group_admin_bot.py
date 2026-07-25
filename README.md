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

3. **Token set karo** — do tarike hain:
   - `group_admin_bot.py` file me `BOT_TOKEN = "PUT_YOUR_BOT_TOKEN_HERE"` line me apna token daal do
   - YA environment variable set karo:
     ```
     export TELEGRAM_BOT_TOKEN="apna_token_yaha"
     ```

4. **Bot ko group me add karo:**
   - Apne group me bot ko add karo
   - Bot ko **Admin** banao in permissions ke saath: Ban users, Delete messages, Pin messages, Add admins

5. **Bot run karo:**
   ```
   python group_admin_bot.py
   ```

## Commands

| Command | Kya karta hai |
|---|---|
| `/kick` | (reply karke) User ko group se nikalta hai — dobara aa sakta hai |
| `/ban [reason]` | (reply karke) User ko permanently ban karta hai |
| `/unban <user_id>` | User ko unban karta hai |
| `/mute [minutes]` | (reply karke) User ko mute karta hai — minutes na do to permanent |
| `/unmute` | (reply karke) User ko unmute karta hai |
| `/warn [reason]` | (reply karke) Warning deta hai, 3 warnings pe auto-ban |
| `/warnings` | User ki warning count dikhata hai |
| `/resetwarns` | (reply karke) Warnings reset karta hai |
| `/promote` | (reply karke) User ko admin banata hai |
| `/demote` | (reply karke) User ko admin se hatata hai |
| `/pin` | (reply karke) Message pin karta hai |
| `/unpin` | Pinned message hatata hai |
| `/purge` | (reply karke) Us message se ab tak sab delete karta hai |
| `/rules` | Group rules dikhata hai |
| `/setrules <text>` | Rules set karta hai |
| `/info` | Apna ya (reply karke) kisi aur ka info dikhata hai |

## Railway pe Deploy Karna

1. **GitHub repo banao** aur ye files (`group_admin_bot.py`, `requirements.txt`, `Procfile`) usme push kar do.

2. **Railway pe naya project banao:**
   - [railway.app](https://railway.app) pe login karo
   - "New Project" → "Deploy from GitHub repo" → apna repo select karo

3. **Environment variable set karo:**
   - Project ke "Variables" tab me jao
   - Add karo: `TELEGRAM_BOT_TOKEN` = `apna_bot_token`
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
- Warnings aur rules abhi **memory me** store hote hain — bot restart hone pe reset ho jayenge. Agar permanent chahiye to database (SQLite/PostgreSQL) add karna padega — bata dena agar wo bhi chahiye.
- 24/7 chalane ke liye ise kisi server/VPS ya Railway/Render jaisi service pe host karna hoga.

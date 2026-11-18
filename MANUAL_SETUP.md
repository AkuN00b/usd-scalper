# Manual konfigurasi USD Scalper di Windows

Mulai dengan dashboard lokal. Tambahkan TradingView kalau lo mau sinyal otomatis masuk. Telegram baru diperlukan kalau lo mau notifikasi BUY/SELL ke HP. Untuk transaksi manual, gunakan akun Exness langsung dari terminal trading.

## 1. Dashboard lokal

Siapkan Python 3.12 atau lebih baru, folder repo, virtual environment `.venv`, dan file `.env`. Pembuatan `.venv` dan token untuk instalasi baru ada di [README](README.md#jalankan-dashboard). Di PowerShell, jalankan dari folder proyek:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Kalau hasilnya `status = ok`, buka [dashboard lokal](http://127.0.0.1:8000/). Kalau server belum aktif, jalankan perintah ini lalu biarkan PowerShell terbuka:

```powershell
.\.venv\Scripts\python.exe -m uvicorn scalper.server:app --env-file .env --host 127.0.0.1 --port 8000 --no-access-log
```

Buka file konfigurasi dengan `notepad .env`. Di dashboard, pilih **Akses data** dan masukkan nilai `DASHBOARD_TOKEN`. Untuk melihat contoh laporan, langsung buka **Backtest** dan pilih XAUUSD atau EURUSD. Untuk catat atau ekspor jurnal, buka akses dashboard terlebih dahulu.

### Isi `.env`

| Pengaturan | Yang lo siapkan |
| --- | --- |
| `DASHBOARD_TOKEN` | Token untuk akses dashboard dan jurnal. Gunakan 32–128 karakter heksadesimal huruf kecil. |
| `WEBHOOK_TOKEN` | Token untuk menerima alert TradingView. Gunakan 32–128 karakter heksadesimal huruf kecil yang berbeda dari token dashboard. Kedua token dibutuhkan saat server mulai. |
| `DB_PATH` | Lokasi SQLite. Pakai `data/scalper.sqlite3` atau lokasi database yang sudah berisi riwayat lo. |
| `ENABLE_PUBLIC_FEEDS` | `1` untuk kalender dan berita otomatis. `0` untuk mode teknikal dengan CSV konteks opsional. |
| `AUTO_BACKUP` | Opsional. Isi `1` untuk menyimpan satu backup per hari UTC saat aplikasi berjalan. Backup otomatis menyimpan tujuh file terakhir. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Opsional. Isi keduanya untuk notifikasi, atau kosongkan keduanya. |
| `CONTEXT_CSV` | Opsional. Isi jalur CSV kalau lo punya tambahan agenda atau berita. Gunakan [format contoh](examples/context.csv). |

Restart server setelah mengubah `.env`. Simpan file tersebut secara lokal. Cadangan database dapat diunduh dari tab **Ringkasan**.

## 2. Sinyal otomatis dari TradingView

1. Pilih satu simbol dan satu feed untuk uji awal, misalnya XAUUSD di feed yang lo pilih. Aktifkan 2FA dan cek bahwa akun lo punya fitur **Webhook URL** dan slot alert strategi. [Webhook TradingView](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/) · [Fitur paket TradingView](https://www.tradingview.com/pricing/)
2. Siapkan alamat HTTPS publik yang meneruskan request ke server. Pakai tunnel saat menguji dari laptop, atau hosting dengan penyimpanan SQLite persisten jika harus tetap jalan saat laptop mati. Jalankan satu worker Uvicorn dan pastikan jam sistem sinkron.
3. Di TradingView, buka chart **candlestick standar 3 menit**. Tempel isi [skrip Pine](tradingview/usd_scalper.pine) di **Pine Editor**, simpan, lalu pilih **Add to chart**. Di pengaturan strategi, isi **Token webhook** dengan nilai `WEBHOOK_TOKEN`.
4. Buat alert. Pada **Condition**, pilih strategi USD Scalper dan **alert() function calls only**. Pada **Notifications**, aktifkan **Webhook URL** dan isi `https://DOMAIN-LO/webhooks/tradingview`. Skrip membuat payload JSON sendiri. [Pilihan kondisi alert TradingView](https://www.tradingview.com/support/solutions/43000597494-alerts-on-alert-function/)
5. Saat pasar aktif, tunggu candle M3 berikutnya tutup dan lihat **Webhook status** di log alert TradingView. Di dashboard, pilih simbol yang sama dan lihat **Riwayat sinyal** dengan filter **Semua** supaya keputusan WAIT juga terlihat. Ulangi proses per simbol yang mau dipantau.
6. Periksa masa aktif alert. Buat ulang alert kalau skrip, simbol, timeframe, atau token diubah. Ubah juga URL alert jika alamat tunnel berubah. [Cara TradingView menyimpan snapshot alert](https://www.tradingview.com/pine-script-docs/faq/alerts/#if-i-change-my-script-does-my-alert-change)

Untuk audit setelah uji, pilih instrumen serta filter BUY, SELL, WAIT, atau BUY/SELL di riwayat. Pilih periode lalu klik **Ekspor sinyal CSV** untuk mengambil semua keputusan yang cocok dalam periode, termasuk yang berada di luar 50 baris terbaru.

### Contoh tunnel sementara

Dengan Uvicorn lokal masih berjalan, pasang `cloudflared` lalu buka PowerShell kedua:

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Salin URL `https://….trycloudflare.com` yang muncul, tambahkan `/webhooks/tradingview`, lalu masukkan ke alert. Quick Tunnel meneruskan aplikasi lokal melalui URL publik. Gunakan untuk uji sementara dan hentikan setelah selesai. Untuk penggunaan rutin, siapkan alamat tetap dan atur akses publik ke rute webhook. Selama setup di laptop, Uvicorn, tunnel, dan koneksi internet harus aktif. [Quick Tunnels Cloudflare](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

### Kalau sinyal belum muncul

| Yang terlihat | Yang dicek |
| --- | --- |
| Webhook belum tercatat di TradingView | Status dan masa aktif alert, kondisi `alert() function calls only`, kesiapan candle, timeframe M3, dan jam pasar. |
| `401` dari webhook | Nilai `WEBHOOK_TOKEN` di server dan input strategi. Buat ulang alert setelah input diperbarui. |
| `422` dari webhook | Payload skrip, zona waktu sistem, dan waktu candle. Alert dengan jeda lebih dari dua menit ditolak. |
| Alamat tunnel gagal | Cek `/health` lokal, proses tunnel, URL HTTPS, dan path `/webhooks/tradingview` pada alert. |
| `WAIT` muncul di riwayat | Buka alasannya. WAIT bisa muncul karena aturan strategi, konteks berita, atau feed wajib yang belum siap. |

### Kalender dan berita

Dengan `ENABLE_PUBLIC_FEEDS=1`, BLS, Fed, dan UN wajib siap. ECB juga wajib untuk simbol yang mengandung EUR. GDELT opsional. Dashboard menampilkan status tiap sumber. Keputusan menjadi WAIT jika feed wajib belum pernah berhasil disinkronkan atau keberhasilan terakhirnya lebih dari 25 menit yang lalu.

Untuk uji alur teknikal di demo, set `ENABLE_PUBLIC_FEEDS=0` lalu restart. Ini menonaktifkan kalender dan berita publik serta pemeriksaan kesiapan feed. Jika `CONTEXT_CSV` diisi, konteks dari CSV tetap dipakai. Saat menguji mode dengan filter berita publik, kembalikan nilainya ke `1` dan tunggu status **Feed wajib siap**.

## 3. Telegram opsional

1. Buat bot dengan `/newbot` di @BotFather. Simpan token bot pada `TELEGRAM_BOT_TOKEN` di `.env`. Untuk chat pribadi, buka bot lalu klik **Start**. [Panduan resmi Telegram](https://core.telegram.org/bots/tutorial#obtain-your-bot-token)
2. Dari folder proyek, jalankan blok ini untuk membaca ID chat pribadi dari pembaruan bot. Perintah membaca token dari file dan mencetak ID chat saja.

```powershell
@'
import json
from urllib.request import urlopen
from dotenv import dotenv_values

token = dotenv_values(".env").get("TELEGRAM_BOT_TOKEN")
if not token:
    raise SystemExit("Isi TELEGRAM_BOT_TOKEN di .env.")
with urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15) as response:
    updates = json.load(response)["result"]
chat_ids = {
    chat["id"]
    for update in updates
    if (chat := update.get("message", {}).get("chat", {})).get("type") == "private"
}
for chat_id in chat_ids:
    print(chat_id)
'@ | .\.venv\Scripts\python.exe -
```

3. Kalau belum ada ID, kirim pesan pendek ke bot dari akun lo lalu jalankan ulang. `getUpdates` tersedia untuk bot yang belum dipasangi webhook Telegram. Isi ID tujuan pada `TELEGRAM_CHAT_ID`, lalu restart server. [Telegram Bot API](https://core.telegram.org/bots/api#getupdates)
4. Tunggu keputusan BUY atau SELL baru, lalu lihat kolom **Telegram** di riwayat. Keputusan WAIT hanya tampil di dashboard.

## 4. Transaksi manual dan jurnal Exness

- Siapkan aplikasi trading Exness atau MT5 yang sudah login ke akun demo untuk uji awal. Kredensial broker tetap di aplikasi trading. USD Scalper hanya menerima alert dan mencatat transaksi yang lo masukkan.
- Cocokkan instrumen, cek quote dan spread di terminal trading, lalu cek waktu, alasan, serta level referensi, SL, dan TP di dashboard. Harga OANDA, feed alert, dan quote Exness bisa berbeda. Lot, batas rugi harian, dan risiko akun masih ditentukan terpisah.
- Setelah ada fill, pilih **Jurnal trade → Catat entry** atau **Catat entry dari sinyal**. Masukkan harga dan waktu fill yang aktual, serta lot, SL, dan TP awal bila ada. Saat posisi ditutup, klik **Catat exit** dan isi fill penutupan. Buka **Hasil broker dan biaya** untuk menambahkan P/L bruto, biaya, dan mata uang akun. Pakai filter tanggal entry UTC, sisi, atau status sebelum **Ekspor CSV** jika hanya butuh sebagian transaksi.
- Waktu pada formulir mengikuti zona waktu browser. Tabel dan CSV memakai UTC. Win rate memakai perubahan harga bruto. R bruto dihitung untuk trade ditutup yang memiliki SL awal.

## 5. Backtest dari data sendiri

Siapkan simbol, periode, data harga, dan asumsi biaya. Pilih arsip tick Exness untuk bid/ask historis, atau CSV M1/M3 dari MT5 dengan zona waktu sumber dan spread. Setelah CLI selesai, buka `report.json` di dashboard. Gunakan **Bandingkan dengan laporan lain** untuk membandingkan hasil dan grafik dengan contoh atau file kedua. CSV konteks historis opsional memerlukan waktu event dan waktu ketika informasinya tersedia. [Perintah backtest dan format laporan](README.md#backtest-dengan-tick-exness)

Gunakan **Drawdown** untuk melihat penurunan dari puncak menurut urutan trade. Di **Transaksi**, gabungkan filter sisi, pemicu exit, dan jam entry UTC, lalu gunakan **Ekspor hasil filter** untuk membawa semua baris yang cocok ke spreadsheet.

## 6. Backup dan uji pemulihan

1. Pilih **Ringkasan → Unduh backup SQLite**. Kalau mengaktifkan backup otomatis, lihat subfolder `backups` di samping database saat ingin mengambil salinannya.
2. Untuk uji pemulihan, salin cadangan ke nama baru di folder `data`, misalnya `data/scalper-restored.sqlite3`. Simpan konfigurasi uji di `data/restore.env` dengan dua token yang berbeda, isi `DB_PATH=data/scalper-restored.sqlite3`, set `ENABLE_PUBLIC_FEEDS=0` dan `AUTO_BACKUP=0`, lalu kosongkan dua pengaturan Telegram.
3. Dari PowerShell baru, jalankan server uji dari folder repo:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn scalper.server:app --env-file data/restore.env --host 127.0.0.1 --port 8001 --no-access-log
   ```

   Buka alamat uji, masuk dengan token dashboard dari `data/restore.env`, dan cek riwayat sinyal serta jurnal. Setelah diverifikasi, gunakan jalur salinan yang sudah diuji sebagai `DB_PATH` di konfigurasi utama jika ingin beralih ke database tersebut. [Dashboard uji pemulihan](http://127.0.0.1:8001/)

Kalau lo cuma mau menjalankan dashboard atau jurnal lokal, cukup selesaikan bagian pertama. Untuk memastikan jalur sinyal berfungsi, uji satu instrumen sampai keputusan M3 pertama muncul di dashboard, lalu lanjutkan ke Telegram atau instrumen tambahan.

# USD Scalper

Sistem analisis dan backtest untuk XAUUSD, EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, dan NZDUSD. TradingView mengirim alert saat candle M3 tutup. Backend memberi keputusan **BUY**, **SELL**, atau **WAIT** beserta alasannya. Dashboard menampilkan keputusan, kalender, berita, dan laporan backtest. Telegram opsional untuk BUY dan SELL.

Eksekusi di akun Exness tetap manual. Sistem menyarankan level Stop Loss dan Take Profit dari harga referensi dan belum menentukan lot, batas rugi harian, atau ukuran risiko akun. Sinyal baru tidak mengubah posisi yang sudah dibuka. Dalam simulasi, posisi yang sudah terbuka tetap mengikuti SL, TP, atau batas 30 menit meski ada sinyal lain atau berita baru.

## Jalankan dashboard

Perlu Python 3.12 atau lebih baru. Dashboard memakai HTML, CSS, dan JavaScript bawaan browser. Jalankan perintah berikut dari folder repo di PowerShell.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath .env)) {
    .\.venv\Scripts\python.exe -c "from pathlib import Path; import secrets; config=Path('.env.example').read_text(); config=config.replace('WEBHOOK_TOKEN=', 'WEBHOOK_TOKEN='+secrets.token_hex(24)).replace('DASHBOARD_TOKEN=', 'DASHBOARD_TOKEN='+secrets.token_hex(24)); Path('.env').open('x', encoding='utf-8').write(config)"
}
.\.venv\Scripts\python.exe -m uvicorn scalper.server:app --env-file .env --host 127.0.0.1 --port 8000 --no-access-log
```

Di Linux atau macOS, gunakan `python3 -m venv .venv` dan ganti `.\.venv\Scripts\python.exe` dengan `.venv/bin/python`. Jalankan baris pembuat token secara langsung saat `.env` belum ada.

Buka dashboard, klik **Akses data**, lalu masukkan `DASHBOARD_TOKEN` dari `.env`. Di PowerShell, buka file itu dengan `notepad .env`. Gunakan **Kelola akses → Kunci data** untuk mengakhiri akses di tab. Untuk mode teknikal lokal, ubah `ENABLE_PUBLIC_FEEDS=0` di `.env` dan restart server. Hentikan server dengan **Ctrl+C**. [Dashboard lokal](http://127.0.0.1:8000/)

Untuk perintah backtest dan pengujian di bawah, jalankan `python` dari virtual environment yang sama. Di PowerShell lo bisa mengganti `python` dengan `.\.venv\Scripts\python.exe`. Di Linux atau macOS, gunakan `.venv/bin/python`.

### Pakai dashboard

- **Pantauan market.** Pilih instrumen di bagian atas atau dari **Watchlist**. Klik **Favorit** untuk menambah atau menghapus instrumen yang sedang dipilih. Grafik **Harga sinyal** memakai harga referensi dari riwayat keputusan M3. Pilih **TradingView** untuk memuat chart OANDA.
- **Tampilan.** Pilih tema terang, gelap, atau sesuai sistem. Lo juga bisa mengubah aksen warna, merapatkan tabel, dan menyembunyikan kalender. Pilihan tersebut, instrumen terakhir, dan watchlist disimpan per browser dan alamat dashboard.
- **Backtest.** Pilih contoh XAUUSD atau EURUSD untuk mencoba tanpa akun TradingView atau token dashboard. Untuk laporan sendiri, gunakan **Buka report.json**. File pilihan dibaca di browser. Grafik menunjukkan R kumulatif untuk seluruh laporan dan filter sisi berlaku pada tabel transaksi.
- **Jurnal trade.** Buka akses untuk mencatat dan menutup trade aktual, melihat statistik bruto, serta mengekspor CSV per instrumen.

## Hubungkan TradingView

1. Di Pine Editor, tempel [tradingview/usd_scalper.pine](tradingview/usd_scalper.pine), simpan, lalu tambahkan ke chart candlestick standar **3 menit**. Lakukan per simbol yang ingin dipantau. Pilih satu feed per simbol agar alert dari feed berbeda tidak bersaing.
2. Buka pengaturan strategi dan isi **Token webhook** dengan nilai `WEBHOOK_TOKEN`. Pakai token khusus endpoint ini.
3. Untuk alert live, gunakan server dengan alamat **HTTPS publik** yang meneruskan ke aplikasi. Deploy satu worker Uvicorn. Pada TradingView, aktifkan 2FA, pastikan paket lo mendukung webhook, dan pilih kondisi strategi **alert() function calls only**. Isi Webhook URL dengan `https://domain-lo/webhooks/tradingview`. Script menghasilkan pesan JSON. Buat ulang alert setelah mengubah input atau script. [Dokumentasi webhook TradingView](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/)
4. Tunggu satu candle M3 tutup, lalu cek dashboard. Riwayat memuat sinyal WAIT juga. Backend menolak alert kedaluwarsa dan menyimpan satu keputusan per simbol per candle.

Panah dan Strategy Tester di TradingView memakai aturan teknikal. Atur asumsi biaya di **Properties** pada TradingView. Filter berita hanya berlaku di backend dan di backtest Python yang diberi CSV konteks historis. Chart OANDA di dashboard adalah chart referensi. Harga chart, feed alert TradingView, dan quote Exness bisa berbeda.

## Backtest dengan tick Exness

Perintah kedua menyimulasikan satu periode historis dan menyimpan waktunya dalam UTC. Pilih arsip yang tersedia untuk simbol dan bulan yang lo perlukan. Bulan pada contoh sudah diuji. [Arsip tick publik Exness](https://www.exness.com/tick-history/)

```sh
python -m scalper.exness --symbol XAUUSD --month 2025-01
python -m scalper.backtest --ticks data/Exness_XAUUSD_2025_01.zip --symbol XAUUSD --from-utc 2025-01-02T00:00:00Z --to-utc 2025-01-09T00:00:00Z --latency-ms 1000 --slippage 0.02 --output data/backtest-xau
```

- Gunakan `--ticks` diikuti beberapa ZIP atau CSV tick Exness untuk uji lebih panjang. Urutkan arsip dari yang terlama. Jika memakai batas, periode mulai inklusif dan akhir eksklusif pada kelipatan 15 menit UTC. Uji membutuhkan setidaknya 50 candle M15 utuh sebagai warm-up.
- Simulasi memakai bid untuk candle, ask untuk entry BUY atau exit SELL, dan bid untuk entry SELL atau exit BUY. Setelah M3 tutup dan delay habis, simulasi entry pada tick pertama yang memenuhi syarat di menit berikutnya. Jika menit itu tidak berisi tick yang memenuhi syarat, entry dilewati.
- SL, TP, atau batas waktu dinilai mengikuti urutan tick. Harga stop dapat meleset saat terjadi gap. `--slippage` adalah penyesuaian harga per eksekusi yang dikenakan pada entry dan exit non-target. `--commission` adalah biaya pulang-pergi per unit harga. Sesuaikan nilainya dengan instrumen dan tipe akun. Contoh memakai slippage 0,02, delay 1 detik, dan komisi 0.
- Tick memberi waktu quote historis yang memicu fill simulasi, dengan presisi sampai milidetik jika tersedia. Harga dan waktu fill akun pribadi bergantung pada eksekusi aktual.

## Alternatif CSV candle MT5

Gunakan ekspor bar M1 atau M3 dari MT5, dengan kolom `<DATE>`, `<TIME>`, `<OPEN>`, `<HIGH>`, `<LOW>`, `<CLOSE>`. Format standar `timestamp,open,high,low,close` juga diterima. Untuk timestamp tanpa offset, isi `--tz` sesuai zona waktu sumber file. `--spread` dalam satuan harga penuh wajib diisi pada mode candle.

```sh
python -m scalper.backtest --csv data/XAUUSD_M1.csv --symbol XAUUSD --input-minutes 1 --tz UTC --spread 0.20 --slippage 0.02 --output data/backtest-mt5
```

Mode candle menyimulasikan entry pada pembukaan bar yang mengikuti sinyal. Jika stop dan target tersentuh dalam satu candle, simulasi mendahulukan stop. Kolom `exit_at_utc` dan `exit_bar_end_utc` memberi awal dan akhir rentang intrabar untuk exit yang waktunya belum bisa dipastikan dari OHLC.

## Baca laporan

- **`trades.csv`** memuat waktu sinyal, entry, exit, spread, harga, pemicu keluar, dan hasil setiap transaksi.
- **`decisions.csv`** memuat semua keputusan M3 yang memenuhi warm-up, termasuk WAIT, alasan, blokir konteks, dan status entry.
- **`report.json`** bisa dibuka dari tab **Backtest** pada dashboard. **1 R** adalah jarak risiko dari harga entry ke stop awal. Statistik memakai satu posisi pada satu waktu per backtest dan tidak memakai saldo atau leverage akun.

### Hasil uji yang disertakan

Data tick Exness untuk 2–8 Januari 2025 UTC, delay entry 1 detik, spread bid/ask arsip, komisi nol, dan tanpa histori berita. Ini satu periode uji dengan strategi teknikal awal.

| Simbol | Transaksi | Win rate | Total | Drawdown maks. | Rincian |
| --- | ---: | ---: | ---: | ---: | --- |
| XAUUSD | 123 | 39,8% | −7,21 R | 14,94 R | [CSV](examples/backtests/xauusd/trades.csv) · [JSON](examples/backtests/xauusd/report.json) |
| EURUSD | 129 | 36,4% | −31,73 R | 42,43 R | [CSV](examples/backtests/eurusd/trades.csv) · [JSON](examples/backtests/eurusd/report.json) |

Keduanya rugi pada periode ini. Slippage simulasi per eksekusi 0,02 untuk XAUUSD dan 0,00001 untuk EURUSD.

## Logika sinyal dan sumber

Arah mengikuti posisi harga terhadap EMA 20 dan EMA 50 pada M15 terakhir yang sudah selesai ketika M3 dibuka. M3 menguji EMA 9/21, breakout candle sebelumnya, RSI 14, dan ukuran candle relatif terhadap ATR 14. Entry perlu konfirmasi EMA, breakout, ATR valid, dan skor minimal 85 dari 100. Stop awal 1,2 ATR dan target 1,5 kali jarak stop. Skor mengukur terpenuhinya aturan strategi.

Backend mengambil kalender BLS, RSS Fed dan ECB, serta berita UN News. GDELT opsional dan status kegagalannya tampil terpisah. Sumber ini dapat digunakan tanpa API key. Kalender hanya memuat rilis BLS yang dicocokkan dan feed berita hanya memuat headline yang terjaring aturan kata kunci. Tambahkan agenda FOMC, ECB, NFP, atau rilis lain yang diperlukan melalui `CONTEXT_CSV` untuk melengkapi cakupan. [BLS](https://www.bls.gov/schedule/) · [Fed](https://www.federalreserve.gov/feeds/feeds.htm) · [ECB](https://www.ecb.europa.eu/home/html/rss.en.html) · [UN News](https://news.un.org/en/) · [GDELT](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)

Rilis relevan berdampak tinggi menahan entry dari 15 menit sebelum sampai 15 menit sesudah jadwal. Headline relevan berdampak tinggi bisa memblokir entry selama 25 menit setelah waktu event setelah tersedia di sistem. Jika feed wajib tidak tersedia atau sudah lebih dari 25 menit sejak sinkronisasi terakhir yang berhasil, keputusan menjadi WAIT. `ENABLE_PUBLIC_FEEDS=0` mengaktifkan mode teknikal dengan CSV lokal opsional.

Untuk backtest berita, tambahkan `--context data/macro-and-news.csv`. File [examples/context.csv](examples/context.csv) menunjukkan format dengan data fiktif. Untuk data sungguhan, `event_at` adalah waktu rilis atau publikasi, sedangkan `known_at` adalah waktu informasi tersedia atau pertama terlihat. Keduanya harus memiliki zona waktu. Backtest hanya menerima informasi yang sudah tersedia pada waktu keputusan. Feed terkini tidak dipakai untuk mengisi histori berita. Tanpa CSV konteks, hasil ditandai teknikal.

## Jurnal trade dan memori proyek

Buka tab **Jurnal trade**, pilih instrumen, lalu klik **Catat entry**. Untuk menautkan alasan dari TradingView, pilih **Catat entry dari sinyal** di keputusan terbaru atau buka baris BUY/SELL di riwayat sinyal. Masukkan harga fill aktual dari Exness. Waktu di formulir mengikuti zona waktu browser yang terlihat di label, sedangkan tabel dan ekspor memakai UTC. Untuk trade yang selesai, klik **Catat exit** dan isi waktu, harga, serta catatan.

Jurnal menampilkan win rate bruto dari perubahan harga dan rata-rata R bruto jika trade ditutup dengan SL awal tercatat. Statistik ini belum memasukkan biaya atau ukuran lot. **Ekspor CSV** mengunduh seluruh jurnal untuk instrumen yang dipilih, termasuk catatan serta alasan sinyal yang ditautkan.

Jurnal tersimpan bersama sinyal di database lokal yang ditunjuk `DB_PATH`. Saat memperbarui proyek, jalankan dengan file database yang sama agar riwayatnya tersedia. Untuk meneruskan diskusi di chat lain, sertakan memori proyek dan, bila perlu, CSV yang ingin dibahas. [Memori proyek](PROJECT_MEMORY.md)

## Telegram dan GitHub

- **Telegram.** Buat bot lewat BotFather, lalu buka chat dengan bot dan mulai percakapan. Isi `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID` di `.env`, lalu restart aplikasi. Status pengiriman tampil di dashboard. Webhook tidak menunggu Telegram. [Telegram Bot API](https://core.telegram.org/bots/api)
- **GitHub.** Workflow `.github/workflows/check.yml` menjalankan pengujian setiap push dan pull request. Aplikasi perlu berjalan di laptop dengan tunnel HTTPS atau di layanan hosting untuk menerima webhook live. [Langkah berikutnya](NEXT_STEPS.md)

```sh
python -m unittest discover -s tests -v
```

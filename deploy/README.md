# 自宅サーバへの配置

Cloudflare Tunnel で外に出す。**ルータのポート開放は不要**、自宅の IP アドレスも表に出ない。
オリジンは平文 HTTP をループバックに開くだけなので、証明書の取得も更新も持たない。

```
   ブラウザ
      │ HTTPS
   Cloudflare のエッジ（TLS 終端・キャッシュ）
      │ トンネル（サーバから外向きに張る。着信ポートなし）
   ┌──┴──────────────── LXC ────────────────┐
   │ cloudflared                            │
   │    ├─ example.com     → 127.0.0.1:8080 │
   │    └─ dev.example.com → 127.0.0.1:8081 │
   │ Caddy                                  │
   │    ├─ :8080 → /srv/mmg/prod/current    │
   │    └─ :8081 → /srv/mmg/staging/current │
   │ mmg-deploy@{prod,staging}.timer        │
   │    2分ごとに git を見て、変わっていれば入れ替える │
   └────────────────────────────────────────┘
```

配信されるのは `git archive` が出す**サイト7ファイルだけ**。`.gitattributes` の
`export-ignore` により `deploy/` も `README.md` も `.git` も web ルートに存在しない。

---

## 1. LXC を用意する

非特権 LXC で足りる。cloudflared は外向きにしか繋がないので TUN も要らない。

```sh
apt update && apt install -y git curl python3 jq
```

## 2. Caddy を入れる

```sh
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
```

## 3. 置き場所と設定

```sh
# デプロイ用スクリプト。配信物とは別に置く。
git clone https://github.com/uewolf25/mobile-minigame.git /opt/mmg

mkdir -p /srv/mmg/{prod,staging} /etc/mmg /var/log/caddy
# Caddy は起動時に csp.caddy を読むので、空でも先に作っておく
touch /srv/mmg/prod/csp.caddy /srv/mmg/staging/csp.caddy

install -m 600 /opt/mmg/deploy/env/prod.conf.example    /etc/mmg/prod.conf
install -m 600 /opt/mmg/deploy/env/staging.conf.example /etc/mmg/staging.conf
$EDITOR /etc/mmg/prod.conf        # REPO_URL などを自分のものに直す

cp /opt/mmg/deploy/Caddyfile /etc/caddy/Caddyfile
cp /opt/mmg/deploy/systemd/mmg-deploy@.* /etc/systemd/system/
systemctl daemon-reload
systemctl restart caddy
```

## 4. 初回デプロイ

```sh
/opt/mmg/deploy/mmg-deploy prod
curl -I http://127.0.0.1:8080/          # 200 が返ればオリジンは出来ている
```

## 5. cloudflared

```sh
cloudflared tunnel login
cloudflared tunnel create mmg
cloudflared tunnel route dns mmg example.com
cloudflared tunnel route dns mmg dev.example.com

cp /opt/mmg/deploy/cloudflared/config.yml /etc/cloudflared/config.yml
$EDITOR /etc/cloudflared/config.yml     # UUID とホスト名を直す
cloudflared service install
systemctl enable --now cloudflared
```

## 6. タイマーを入れる

```sh
systemctl enable --now mmg-deploy@prod.timer mmg-deploy@staging.timer
systemctl list-timers 'mmg-deploy@*'
```

`main` に入った変更は2分以内に本番へ、`staging` ブランチは検証側へ流れる。

---

## Cloudflare 側で必ず切るもの

CSP をハッシュで固めてあるので、**オリジンの HTML を1バイトでも書き換える機能を
有効にすると、ハッシュが合わなくなって全ゲームが白画面になる。**

| 機能 | 設定 | 理由 |
| --- | --- | --- |
| Rocket Loader | **OFF** | インライン script を注入する |
| Email Address Obfuscation | **OFF** | script を注入する |
| Auto Minify（残っていれば） | **OFF** | HTML を書き換えてハッシュが崩れる |
| Web Analytics / Browser Insights | **OFF** | ビーコンを注入する。仕様の「外部解析なし」にも反する |
| SSL/TLS | **Full (strict)** | |
| Always Use HTTPS | **ON** | |
| Brotli | ON でよい | 転送時の圧縮だけで中身は変わらない |

`dev.example.com` は Cloudflare Access（Zero Trust）で自分だけに絞っておく。
`X-Robots-Tag: noindex` も付けてあるが、Access のほうが確実。

apex と www の両方を使うなら、正規化は Cloudflare の Redirect Rules 側でやる。
オリジンはトンネルが振ったものしか見えないので、こちらでは判断できない。

---

## 運用

```sh
# 今どのコミットが出ているか
basename "$(readlink -f /srv/mmg/prod/current)"

# ログ
journalctl -u mmg-deploy@prod -n 50
tail -f /var/log/caddy/prod.log
```

### 手で切り戻す

**先にタイマーを止めること。** 止めずに戻すと2分後に `main` が再び上書きする。

```sh
systemctl stop mmg-deploy@prod.timer

cd /srv/mmg/prod
ls -1t releases/                                   # 戻す先を選ぶ
ln -sfn releases/<sha> current.new && mv -T current.new current
/opt/mmg/deploy/csp-hashes.py current csp.caddy    # CSP も一緒に戻す
systemctl reload caddy
```

直したら `git revert` して `main` に入れ、タイマーを再開する。

### スクリプト自体を更新する

`/opt/mmg` は**わざと自動更新にしていない**。配信対象のリポジトリが
デプロイ機構そのものを書き換えられると、事故と侵害の両方の経路になる。

```sh
git -C /opt/mmg pull
cp /opt/mmg/deploy/Caddyfile /etc/caddy/Caddyfile && systemctl reload caddy
```

### CSP を段階的に上げる

最初は `/etc/mmg/prod.conf` の `CSP_MODE=report-only` で流し、6ページすべてを
開いて DevTools のコンソールに違反が出ないことを見てから `enforce` に上げる。

違反の送信先（`report-uri`）は**あえて置いていない**。仕様が「サーバー送信なし」と
決めているので、確認はブラウザのコンソールだけで行う。

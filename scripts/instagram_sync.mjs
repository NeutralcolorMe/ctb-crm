/* Instagram feed sync — pulls latest @ctb.realty posts via the Instagram API
   (Instagram Login flavour) and publishes instagram/instagram.json + self-hosted
   images under instagram/media/ for the website's Instagram band.

   Run by .github/workflows/instagram-sync.yml on a daily schedule.
   Requires: env IG_ACCESS_TOKEN — a long-lived Instagram user access token
   for the ctb.realty professional account (see instagram/README.md). */

import { writeFile, readFile, readdir, unlink, mkdir } from 'node:fs/promises';
import path from 'node:path';

const API = 'https://graph.instagram.com';
const OUT_JSON = 'instagram/instagram.json';
const MEDIA_DIR = 'instagram/media';
const LIMIT = 12;

const token = process.env.IG_ACCESS_TOKEN;
if (!token) {
  console.log('IG_ACCESS_TOKEN not set — skipping sync (see instagram/README.md to connect the account).');
  process.exit(0);
}

async function api(pathname, params = {}) {
  const url = new URL(API + pathname);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  url.searchParams.set('access_token', token);
  const res = await fetch(url);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`IG API ${pathname} -> HTTP ${res.status}: ${JSON.stringify(body.error || body).slice(0, 300)}`);
  return body;
}

const profile = await api('/me', { fields: 'username,name' });
const feed = await api('/me/media', {
  fields: 'id,caption,media_type,media_url,thumbnail_url,permalink,timestamp',
  limit: String(LIMIT),
});

const posts = [];
await mkdir(MEDIA_DIR, { recursive: true });

for (const m of feed.data || []) {
  const src = m.media_type === 'VIDEO' ? (m.thumbnail_url || m.media_url) : m.media_url;
  if (!src || !m.permalink) continue;
  const file = `${MEDIA_DIR}/${m.id}.jpg`;
  try {
    const res = await fetch(src);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    await writeFile(file, Buffer.from(await res.arrayBuffer()));
  } catch (e) {
    console.warn(`media download failed for ${m.id} (${e.message}) — keeping CDN URL as fallback`);
  }
  posts.push({
    id: m.id,
    type: m.media_type,
    caption: (m.caption || '').slice(0, 400),
    permalink: m.permalink,
    media: `${MEDIA_DIR}/${m.id}.jpg`,
    mediaUrl: src,
    timestamp: m.timestamp,
  });
}

if (!posts.length) {
  console.log('API returned no posts — leaving existing feed untouched.');
  process.exit(0);
}

// prune media files for posts that dropped out of the feed
const keep = new Set(posts.map((p) => `${p.id}.jpg`));
for (const f of await readdir(MEDIA_DIR)) {
  if (f.endsWith('.jpg') && !keep.has(f)) await unlink(path.join(MEDIA_DIR, f));
}

const prev = JSON.parse(await readFile(OUT_JSON, 'utf8').catch(() => '{}'));
const db = {
  updated: new Date().toISOString(),
  profile: {
    username: profile.username || 'ctb.realty',
    name: profile.name || prev.profile?.name || 'CTB Realty',
    url: `https://www.instagram.com/${profile.username || 'ctb.realty'}/`,
  },
  posts,
};
await writeFile(OUT_JSON, JSON.stringify(db, null, 1) + '\n');
console.log(`Published ${posts.length} posts to ${OUT_JSON}.`);

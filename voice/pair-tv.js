// One-time pairing with the TV's remote-control channel. The TV shows an "Allow" pop-up;
// once allowed, it returns a token that Jarvis uses for power on/off. Sends no key presses.
// node --experimental-websocket pair-tv.js
const fs = require('fs'), path = require('path');
process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';   // the TV uses a self-signed certificate on the local network
const env = Object.fromEntries(fs.readFileSync(path.join(__dirname, 'corelynx.env'), 'utf8')
  .split(/\r?\n/).filter(l => /^[A-Z_]+=/.test(l)).map(l => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1).trim()]));
const name = Buffer.from('Jarvis').toString('base64');
const url = `wss://${env.TV_IP}:8002/api/v2/channels/samsung.remote.control?name=${name}`;
console.log('Waiting for you to press Allow on the TV (2 min)...');
const ws = new WebSocket(url);
const timer = setTimeout(() => { console.log('No answer from the TV within 2 minutes.'); process.exit(1); }, 120000);
ws.onmessage = (m) => {
  let d; try { d = JSON.parse(m.data); } catch (e) { return; }
  if (d.event === 'ms.channel.connect') {
    clearTimeout(timer);
    const token = d.data && d.data.token;
    if (token) { fs.writeFileSync(path.join(__dirname, 'tv-token.txt'), token); console.log('Paired. Token saved to voice/tv-token.txt'); }
    else console.log('Connected, but the TV returned no token (it may already trust this laptop).');
    ws.close(); process.exit(0);
  } else if (d.event === 'ms.channel.unauthorized') {
    clearTimeout(timer); console.log('The TV refused the connection (Deny was chosen, or remote access is off).'); process.exit(1);
  }
};
ws.onerror = () => { clearTimeout(timer); console.log('Could not reach the TV remote-control channel.'); process.exit(1); };

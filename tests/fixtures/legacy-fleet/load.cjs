// Published snapshot generated 2026-10-19T17:05:02Z: stable inputs for legacy reading compatibility.
const fs = require('node:fs');
const path = require('node:path');
const read = name => JSON.parse(fs.readFileSync(path.join(__dirname, name), 'utf8'));
module.exports = { manifest: read('manifest.json'), fleet: read('fleet.json'), events: read('events.json').events };

// Parse .map files and extract original source paths + key excerpts
const fs = require('fs');

if (process.argv.length < 3) {
    console.log('Usage: node sourcemap-parser.js <file.map> [filter]');
    process.exit(1);
}

const mapFile = process.argv[2];
const filter = process.argv[3] || '';
const data = JSON.parse(fs.readFileSync(mapFile, 'utf8'));

console.log('Version:', data.version);
console.log('File:', data.file);
console.log('Sources:', data.sources.length, 'files');
console.log('Mappings:', data.mappings ? data.mappings.length : 0, 'chars');
console.log('');

// List all source files
const sources = data.sources.filter(s => s.includes(filter));
console.log('=== Source Files ===');
sources.forEach(s => console.log('  ' + s));

// If sourcesContent is embedded, show key files
if (data.sourcesContent) {
    let totalSize = 0;
    data.sourcesContent.forEach((c, i) => {
        if (c) {
            const path = data.sources[i];
            const size = c.length;
            totalSize += size;
            if (filter && !path.includes(filter)) return;
            // Show large/interesting files
            if (size > 500 || path.includes('api') || path.includes('router') || 
                path.includes('store') || path.includes('auth') || path.includes('login')) {
                console.log('');
                console.log('=== ' + path + ' (' + size + ' chars) ===');
                console.log(c.substring(0, 500));
            }
        }
    });
    console.log('');
    console.log('Total embedded source size:', totalSize, 'chars');
}

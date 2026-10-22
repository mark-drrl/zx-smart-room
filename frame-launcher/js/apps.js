/*
 * Your apps, laid out as a bento grid (7 columns x 3 rows, see .bento in css/home.css):
 *
 *   yt yt nf nf cal cal cal      yt = YouTube 2x2, nf = Movies 2x2,
 *   yt yt nf nf cal cal cal      cal = calendar widget 3x2,
 *   pv pv sp at ga  br  mt       pv = Prime Video 2x1, the rest 1x1
 *
 * area:    which grid cell(s) the app fills
 * tint:    the app's colour; big tiles glow faintly with it
 * tizenId: what the TV uses to open the app; hubId names the icon file
 * YouTube opens TizenTube (ad-free YouTube, installed 22 Oct with the Partner certificate)
 * link:    opens your private link (frame-launcher/private/link.txt, never in git) in the TV browser
 */
var APPS = [
  { title: 'YouTube',     tizenId: 'xvvl3S1TT1.TizenTubeStandalone', hubId: '111299001912',  area: 'yt', tint: '#FF1F3D' },
  { title: 'Movies',      link: true,                          hubId: 'movies',        area: 'nf', tint: '#F08A3C' },
  { title: 'Prime Video', tizenId: 'org.tizen.primevideo',      hubId: '3201910019365', area: 'pv', tint: '#1A8CFF' },
  { title: 'Spotify',     tizenId: 'rJeHak5zRg.Spotify',        hubId: '3201606009684', area: 'sp', tint: '#1ED760' },
  { title: 'Apple TV',    tizenId: 'com.samsung.tv.aria-video', hubId: '3201807016597', area: 'at', tint: '#B9C6E4' },
  { title: 'Gallery',     tizenId: 'com.samsung.tv.gallery',    hubId: '3201710015037', area: 'ga', tint: '#FF3C8A' },
  { title: 'Browser',     tizenId: 'org.tizen.browser',         hubId: '3202010022079', area: 'br', tint: '#5B5CF6' },
  { title: 'Google Meet', tizenId: 'IewQEY143t.GoogleDuoWeb',   hubId: '3202008021439', area: 'mt', tint: '#00AC47' }
];

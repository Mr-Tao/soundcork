## radio-browser.info

`radio-browser.info` is a community-driven internet radio directory. SoundCork
uses it as an anonymous BMX catalog provider for live radio stations.

## SoundCork integration

SoundCork exposes Radio Browser through its local BMX registry:

- service name: `RADIO_BROWSER`
- provider id: `39`
- local service URL: `{BMX_SERVER}/bmx/radio-browser`
- stream type: `liveRadio`

The speaker and Stockholm app talk to SoundCork. SoundCork then queries the
Radio Browser API server-side and maps station data into the same BMX navigation
and playback response shape used by TuneIn-style catalog services.

Implemented local endpoints:

```text
GET  /bmx/radio-browser
GET  /bmx/radio-browser/v1/navigate
GET  /bmx/radio-browser/v1/navigate/{encoded_uri}
GET  /bmx/radio-browser/v1/search?q={query}
GET  /bmx/radio-browser/v1/playback/station/{stationuuid}
POST /bmx/radio-browser/v1/report
```

Radio Browser station UUIDs are used as stable identifiers. The legacy numeric
`id` fields are intentionally not used because they are not stable across Radio
Browser API mirrors.

Navigation and search responses intentionally expose station playback items as:

```xml
<ContentItem source="RADIO_BROWSER" type="stationurl" location="/stations/byuuid/<uuid>"/>
```

That is the SoundTouch-compatible station location shape. The
`/bmx/radio-browser/v1/playback/station/{stationuuid}` endpoint remains
available as a server-side BMX playback response helper, but that URL should not
be used as the `ContentItem` location sent to a speaker.

## API behavior

SoundCork follows the public Radio Browser API guidance:

- use `stationuuid` rather than legacy numeric ids;
- send a descriptive `User-Agent`;
- keep the API base URL configurable;
- call `/json/url/{stationuuid}` when a station starts playback so Radio
  Browser can count the click.

Station selection is conservative for SoundTouch compatibility:

- only stations with `lastcheckok=1` and an HTTP(S) stream URL are listed;
- `url_resolved` is preferred over the submitted `url`;
- MP3/AAC streams are ranked above Ogg/Vorbis and FLAC for initial playback
  compatibility;
- HLS and FLAC are not globally blocked, but they are not preferred unless they
  are the best available candidate.

## Configuration

Optional settings:

```text
RADIO_BROWSER_API_BASE_URLS=https://all.api.radio-browser.info
RADIO_BROWSER_USER_AGENT=SoundCork/1.0 (+https://github.com/deborahgu/soundcork)
```

`RADIO_BROWSER_API_BASE_URLS` can contain a comma-separated list. SoundCork tries
the configured entries in randomized order.

## Direct speaker experiment

The `RADIO_BROWSER` source must be present in the speaker's active sources
before these content items can be selected. If the source was added while the
speaker was already running, send a `sourcesUpdated` notification or reboot the
speaker before testing playback.

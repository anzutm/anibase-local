# Third-Party Notices

This project includes third-party software and assets that remain under their
respective licenses

## Plyr

Files:

- `static/plyr.css`
- `static/plyr.js`
- `static/plyr.svg`

Plyr is licensed under the MIT License.

Project: https://github.com/sampotts/plyr

## FFmpeg

Windows release archives include FFmpeg and FFprobe. They remain subject to
the license shipped as `tools/FFMPEG_LICENSE.txt`.

Project: https://ffmpeg.org/

## JavascriptSubtitlesOctopus / libass-wasm 4.1.0

Files: `static/vendor/subtitles-octopus/` (unmodified WebAssembly renderer,
worker, and browser integration from the upstream npm distribution).
`default.woff2` is the fallback font from the same upstream release's `assets/`.
The legacy asm.js build is not included; unsupported browsers use WebVTT.

The JavaScript integration is MIT licensed. The WebAssembly bundle includes
libass, FreeType, Fontconfig, HarfBuzz, FriBidi, Expat, Brotli and Liberation
Fonts, under their respective licenses, including LGPL-2.1-or-later.
Full copyright and license texts are in that directory's `COPYRIGHT` and
`LICENSE` files. The renderer is loaded separately and can be replaced there.

Upstream source and build instructions (including dependency revisions):
https://github.com/libass/JavascriptSubtitlesOctopus/tree/4.1.0

Distribution: https://www.npmjs.com/package/libass-wasm/v/4.1.0

## Inter

`static/vendor/subtitles-octopus/InterVariable.woff2` is used only when an
ASS subtitle does not provide its own font. It is Inter Variable from the
official Inter project, licensed under the SIL Open Font License 1.1. The
complete license text is in `static/vendor/subtitles-octopus/INTER-OFL.txt`.

Project: https://github.com/rsms/inter

# Network use and privacy

Varda works offline. Opening, viewing, measuring, analyzing and exporting
images never use the network. The only connection Varda makes without you
asking for something online is the **update check**, and you can turn it off.
Everything else on this page happens only when you use the feature named.

Varda sends no usage statistics, crash reports or identifiers anywhere.

## Every connection Varda makes

| When | Where to | What is sent | What comes back |
| --- | --- | --- | --- |
| **Update check**, 5 s after Varda starts (unless turned off), and **Help → Check for Updates…** | `api.github.com` (the latest release of `varda-pro/Varda`) | an HTTPS request with the header `User-Agent: Varda/<version>` | the latest release's version and page |
| **Find Data**: searching a catalog | the catalog you choose: the PDS Orbital Data Explorer (`oderest.rsl.wustl.edu`), a SpatioTemporal Asset Catalog (STAC, e.g. the USGS Astrogeology catalog on `amazonaws.com`, Element 84's Earth Search), or a footprint file's server | your search: the area, dates, collection and filters | footprints and product lists |
| **Find Data**: downloading products | the product's server, as listed by the catalog | a request for that file | the file |
| **Find Data**: the map's basemap | NASA's Solar System Treks (`trek.nasa.gov/tiles`) | requests for the map tiles in view | tiles; offline, the low-resolution basemap bundled with Varda is shown instead |
| **Kindle a Feature** | the AI provider you choose: Anthropic (`api.anthropic.com`), OpenAI (`api.openai.com`), or a server you name (e.g. Ollama or LM Studio on your own computer, `127.0.0.1`) | your description of the feature, the plugin's files and check results, Varda's plugin guide, and your key | the AI's replies |
| **Processing → Optional Features**: installing PyTorch | the Python Package Index and PyTorch's package server (`pypi.org`, `download.pytorch.org`), through the bundled `uv` | requests for the pinned packages | the packages |
| **Optional Features**: setting up the Ames Stereo Pipeline | GitHub (`github.com/NeoGeographyToolkit/StereoPipeline/releases`) | a request for the release file | the download |
| **Help** menu links (Documentation online, Cite Varda's links, a release page) | opened in your web browser, not by Varda | what your browser sends | |

Each row starts only from the action in its first column. Find Data, for
example, contacts nothing until you open it, and the map asks for tiles only
while it is shown.

## Turning off the update check

Uncheck **Check for updates when Varda starts** in the window that announces
an update, or in the one **Help → Check for Updates…** shows. Varda then makes
no connection on its own. The check sends nothing about you or your data:
only the request for the latest release, with Varda's version in the
`User-Agent` header.

## Where AI keys are kept

Keys for Kindle a Feature are stored in your operating system's keychain
(macOS Keychain, Windows Credential Manager, or the Secret Service keychain
on Linux), through the `keyring` library, one key per provider. They are
never written to Varda's settings, sessions or logs, and are sent only to the
provider they belong to. **Plugins → AI Assistant Settings… → Remove Key** removes one.

## Icons and fonts

Varda's icons and fonts are bundled with it. (In Varda 0.10.0 and earlier the File
menu's icons were looked up on `api.iconify.design`, a Cloudflare address,
at every start; they are now files in Varda.)

## Checking it yourself

Run Varda with your firewall blocking it, or with no network: everything
except the features in the table works, and those say that they could not
connect. A failed update check is recorded in Varda's log, in its app-data
folder next to `autosave.varda` (see [Sessions](sessions-and-saving.md)).

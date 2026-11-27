# Changelog

All notable changes to wc3files are listed here, newest first. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
version numbers follow [Semantic Versioning](https://semver.org/). Numbers
in parentheses are GitHub issues.

Each pull request adds its line under [Unreleased]. A release renames that
section to the new version and its date.

## [Unreleased]

### Fixed

- An attachment's visibility track written to MDL is read back; it was skipped as a node key, so every attachment lost its visibility through MDL (#355).
- Particle emitter flags are written to MDL: Unshaded, SortPrimsFarZ, LineEmitter, Unfogged, ModelSpace and XYQuad on a ParticleEmitter2, and EmitterUsesMDL and EmitterUsesTGA on a ParticleEmitter, which the reader knew but the writer left out (#356).
- Particle and ribbon emitter tracks are written to MDL and read back: a field with a track (visibility, emission rate, speed, alpha, colour and the others) was written as its static value only. A ribbon's texture slot is written too. A static value an MDX model stores beside a track is not kept, since MDL holds one or the other; on 300 corpus models, 72 now come back byte for byte, against 43 (#357).
- Geoset animation flags survive MDL: a static colour was written only when bit 1 (DropShadow) was set, though bit 2 is the colour flag, so most models lost their colour, and DropShadow was never written. Bits 8 and 16, set on 1,063 geoset animations of 300 corpus models and with no MDL name, are kept in a `// wc3files: UnnamedFlags` comment that other MDL tools skip and wc3files reads back (#358).
- A geoset's Unselectable flag is written to MDL (it was read but never written), and the level of detail and name of a version 900 or later geoset are written and read back, as mdx-m3-viewer writes them (#359).
- Texture animations are written to MDL: the reader knew `TextureAnims`, but the writer never wrote them, so every texture animation was lost and a layer's `TVertexAnimId` pointed at nothing (#360).
- A camera's tracks are written to MDL and read back: its position and rotation (KCTR, KCRL) and its target's position (KTTR), as mdx-m3-viewer writes them (#361).
- Material and layer fields of version 900 and later are written to MDL and read back, in the syntax mdx-m3-viewer uses: the material's shader, and the layer's emissive gain and fresnel colour, opacity and team colour, static or animated (KMTE, KFC3, KFCA, KFTC). The flags TwoSided on a material, Unlit on a layer and SortPrimsNearZ, which had no MDL name here, are written and read too (#362).

## [0.11.0] - 2026-11-27

### Added

- MCP tool `compile_script`: vJass, Zinc, cJass or Luck code, a source file, a folder of Luck sources or a map's `war3map.j` compiled to plain JASS, with the compile error and its line as data, and the JASS checker's findings on the result, leaks included. `language: jass` only checks. `check_script` findings now carry only the keys of their schema, as the structured result already did (#334).
- MCP tool `run_script`: a map's script, or a `.lua` or `.j` file, run against the game API stubs of `run-map`, returning the runtime errors, argument warnings and displayed messages, with the game seconds, players and a step limit as arguments (#335).
- MCP tool `compare_maps`: what changed between two versions of a map: members added, removed and changed, objects with each changed field's old and new value, and strings by the stable keys of #224; one part at a time for a large map (#336).
- MCP tool `map_image`: a map's minimap or lobby preview as a PNG image the assistant sees, the map's own or drawn from the terrain, with a text part naming the source (#337).
- MCP tools `game_files`, `game_file` and `game_image`, and the server option `--game`: the installed game's files found by path, read as data, a model summary or text, and its textures shown as PNG; `map_image` draws a minimap in the game's tileset colours when the server has the game (#338).
- MCP tool `replay_actions`: actions and APM per player, the most frequent kinds of action, the actions matching a player, action or object id with their game time, and the replay's W3MMD stats (#339).
- MCP tool `missing_files`: textures a map's models use that neither the map nor the installed game has, and imports nothing in the map names, images, models, sounds and other files alike, a model named with either extension and its `_Portrait` counted with it (#340).
- MCP tools `map_translation`, which lists a map's text under the stable keys of #224, and `apply_translation`, an edit tool that writes `{key: text}` back into war3map.wts, the object data and the map info, refusing keys the map does not have (#341).
- MCP tool `optimize_map`, an edit tool: the passes of `wc3files optimize` (unused objects, computed text, model squeezing, editor-only files) with a report of what each did and the sizes before and after, or a dry run that only reports (#342).
- MCP tool `set_icon`, an edit tool: a picture becomes an object's icon in one call, with its border, its disabled version where the game looks for it, the import entries and the icon field (#343).

## [0.10.0] - 2026-11-27

### Added

- `jass --check` reports handle leaks in JASS, as warnings, and `wc3files.scripts.leaks.find_leaks` gives them as findings: a new location, group, force, effect, text tag, rect or other leaking handle dropped at once, a local never destroyed in its function, and a global no function destroys. The types and their creating, destroying and keeping functions are data, in `scripts/gamedata/leaks.json`. The GUI idioms (`bj_wantDestroyGroup`, `GetLastCreated...()` followed in order), handles passed to the script's own functions, the editor's `gg_` globals and code that runs once at map start are not reported; `--no-leaks` leaves the warnings out, and the MCP `check_script` tool includes them. On the 319 parsed corpus scripts it reports 70,806 warnings in 274 maps, and 19 of 20 drawn at random were real leaks (#255).
- `wc3files lua-bundle` and `wc3files.scripts.lua_bundle`: a Lua project of modules joined by `require` bundled into one `war3map.lua`, following each literal `require("name")` to `name.lua` or `name/init.lua`, each module a function in a table with a local `require` that runs it once. With `--map` the bundle goes in front of the map's generated code, which is kept, and the entry module starts at the end of `main` or, with `--at load`, when the script loads; running it again replaces the bundle. The result is checked with the Lua checker. Put into the 22 corpus maps with a `war3map.lua`, a test project ran on all 15 whose own script reaches the end of `main`, and with `--at load` on the three obfuscated ones with no `main` (#256).
- `wc3files map-translate` and `wc3files.w3x.translation`: the strings of a map under keys that stay the same between versions (`w3u/h000/Name`, `w3a/A000/Ubertip/2`, `w3i/players/3/name`, `script/Trig_Intro_Actions/2`), a diff of two versions by key, and translation files keyed the same way. A file made for an older version carries its translations over where the source text is unchanged and marks the rest; `apply` writes them into `war3map.wts`, or the object data and map info for inline text. Between Balanced Hero Survival 0.91 and 0.92, 1,097 strings changed their `TRIGSTR_` number and kept their key; on all 342 readable corpus maps, an applied translation came back under every key, 1,462,826 strings in all (#224).
- `wc3files cjass` and `wc3files.cjass`: cJass, the C-like JASS dialect of AdicHelper, compiled to JASS with vJass blocks and then through the vJass compiler to plain JASS, following the cJass 1.4.0.2 manual: braced blocks, functions and methods written return type first, locals anywhere, optional `call`/`set`/`local`, compound operators, `++` and `--` in expressions, `whilenot`/`while`/`do`, and the preprocessor (defines with arguments, overloads, private defines, `##` and stringizing, predefined macros, `#if`, `include` with `#guard`, `enum`). `cj_types.j` and `cj_typesEx.j` are bundled, written for this package. The examples of the manual give the JASS it shows for them, and two real cJass projects from GitHub compile with no issue from the JASS checker (#227).
- `wc3files luck` and `wc3files.luck`: Luck, the indentation-based language of LuckyParser, compiled to plain JASS following its language reference, with LuckyParser's generated names: namespaces, both function forms, default and named arguments, locals and globals, `if`/`elif`/`else`, `while`, `loop`, `foreach`, `for ... in a..b`, `print` with conversions, `onInit`/`onLoad` blocks run by `LuckyParser_InitAll`, array-backed structs, `handle.id`, and `//! language` islands of JASS, vJass, Zinc and cJass. Functions are ordered after the ones they call; `--map` puts the code into a map and calls the init blocks from `main`. LuckyParser's own example project compiles with no issue from the JASS checker and, run in the JASS interpreter, prints every line its source says it prints (#228).

### Changed

- The README and the MCP guide link the server's pages on Glama and MCP Rush, and the README shows the MCP Rush card (#332).

## [0.9.0] - 2026-11-27

### Added

- `wc3files terrain-image` and `wc3files.w3e.images`: the ground height, water level and water flag of `war3map.w3e` as grayscale PNG, one pixel per tile corner with north up, and back. Height and water are 16-bit images of the stored values, so export then import gives the same file; `--stretch` spreads them over the gray range to look at, and `--range` maps an image made elsewhere onto chosen values. `read_png_gray` and `write_png_gray` read and write 8 and 16-bit grayscale PNG. On all 342 readable corpus maps, every layer exported and imported, stretched or not, gave back `war3map.w3e` byte for byte (#205).
- `wc3files shadow-image` and `wc3files.shd.image`: `war3map.shd` as a PNG, one pixel per cell (four per tile in each direction), north up, black for shadow; and back, a pixel darker than half gray being shadow, with the size checked against the map's `war3map.w3e`. The map is rebuilt without its stale `(attributes)`. All 342 readable corpus maps have a shadow map of exactly that size holding only 0 and 0xFF, and every one came back byte for byte (#203).
- `terrain-image` takes the cliff level, ground textures and cliff types too: `cliff-level` in 16 gray steps, and `ground` and `cliff-type` as one colour per entry of the map's tileset list, with a JSON legend of colour to tileset code. A painted image can bring in a new tileset code, which is added to the list; `--nearest` takes the closest legend colour for smoothed edges, and `--variation random` gives each changed corner a variation drawn from how often each is used in 342 corpus maps. All six layers came back byte for byte on all 342 readable maps, three of which name a tileset twice. Ramps are not set, and a terrain painted this way has not been checked in the game (#206).
- `wc3files shadow-compute` and `wc3files.shd.compute.compute_shadow_map`: `war3map.shd` made from the terrain and the doodads whose shadow flag is set, in place of the World Editor's Calculate Shadows. The editor's algorithm is not documented, so this is a model fitted to the shadow maps it wrote: the light from the south-west at 30 degrees, cliffs as walls, destructables (trees among them) casting nothing, and each doodad its model at rest, placed, turned and scaled, solid up to its top. The doodad table and models come from the installed game (`--game`), a model imported into the map first; without it only the terrain casts. One sweep over the cells makes it fast: a few seconds for a 256 by 256 map. Against the editor's shadow maps of 64 corpus maps, the shadow cells overlap with a median intersection over union of 0.67 (terrain alone 0.55), and a median 96% of cells agree; some maps' shadow maps are older than their terrain (#202).
- BLP1 files are written with JPEG compression too: `BlpFile.from_rgba(..., compression='jpeg', quality=75, mipmaps=True)` and `--blp-jpeg QUALITY` on `img-convert`, `img-convert-batch`, `make-icon` and `make-icon-set`. `wc3files.img.jpeg_encoder` is a baseline JPEG encoder in pure Python that writes the four planes (blue, green, red, alpha) as the game's files hold them, with the stream split between a shared header and each mip in the same way; at quality 68 its header is byte for byte that of the game's `BTNMountainKing.blp`. This package's decoder and Pillow read the written mips within 1 of each other on every plane. A 1024 by 1024 texture with its mipmaps takes about 18 s (#261).
- `wc3files minimap` and `wc3files.w3e.minimap`: `war3mapMap.blp` rendered from the terrain, into a map or to a `.blp` or `.png`. Like the World Editor's on all 342 readable corpus maps, it is the playable area at 256 by 256, scaled with its shape kept, on black, as a JPEG BLP of one mip at quality 40. The ground blends each tile's corners in their tileset texture's mean colour from the installed game (`--game`), water takes the tileset's minimap tint, and trees and other objects with a minimap colour are dots. Against the editor's minimaps of 266 corpus maps, the median mean absolute difference per channel is 19.8 of 255, against 24.6 for a flat image of each one's own mean colour (#209).
- `wc3files map-preview` and `wc3files.w3x.preview`: `war3mapPreview.tga`, the lobby picture, at 256 by 256 from the map's minimap with the icons of `war3map.mmp` on it (gold mines and neutral buildings in the game's minimap icons with `--game`, start locations in their player's colour), or from any picture with `--image`. The `.mmp` icons sit in minimap pixels: on five corpus maps a gold mine's icon is within a pixel of where the minimap puts the mine. No map info flag goes with a preview: over 342 corpus maps, the 50 with one set no bit the others do not. Every readable corpus map got a preview, 10,395 icons in all (#210).
- `wc3files loading-screen` and `wc3files.w3x.loading_screen`: a custom loading screen from one picture: textures cut from it as JPEG BLP, the model that shows them, both imported and listed in `war3map.imp`, and the map info pointed at the model (number -1, as all 79 corpus maps with their own model have). `classic` is the game's own layout, four quads of a 1024 by 768 picture as its campaign loading screens draw them; `reforged` is one full-screen texture, as 72 of the 79 corpus models are, and the model built is the same as theirs apart from the texture's name. Neither has been checked in the game from this tool (#211).
- `wc3files placed-code` and `wc3files.w3x.placed_code`: a map's placed units and items, destructables and regions as a JASS or Lua function that creates them at run time, with the statements the World Editor writes for them (`--classic` for the natives before 1.32). Over 256 corpus maps, 91.5% of the editor's statements for their placed objects come out the same and 97.2% of those written here are in the editor's scripts. Doodads, start locations, random units, item drop tables and terrain are left out and listed: no native places a doodad or sets a height, a cliff or water. A long JASS function is cut into parts run with `ExecuteFunc`, each with its own operation budget. The output for all 342 readable corpus maps passes the JASS and Lua checks. The autocast orders of 66 abilities are bundled from the game's ability tables (#251).

## [0.8.0] - 2026-11-27

### Added

- `wc3files model-extents` and `wc3files.mdl.extents.compute_extents`: the extents of each geoset, sequence and model recomputed from the vertices, posed by the bones at frames spread over each sequence, with a geoset counted only while its geoset animation shows it. A geoset never seen in a sequence gets the game's empty box, and a model with no vertex keeps its extents. The radius is the farthest vertex from the box's center, or half the diagonal with `--radius diagonal`. `wc3files.mdl.animation` samples tracks and poses vertices, and `wc3files.mdl.batch` runs an edit over models and folders, to `-o` or `--in-place`, for every model command. Over 49 of the game's SD models, a geoset's box in a sequence is within 2% of the stored one in 72% of cases and within 10% in 87%; over 23 HD models, a sequence's box is within 2% in 83% and within 10% in 98%. Where they differ, the box made here is mostly the larger one (#236).
- `wc3files model-transform` and `wc3files.mdl.transform`: `scale_model` and `translate_model` scale a whole model about its origin or move it: vertices, pivots, translation keys, extents, cameras, collision shapes, particle and ribbon sizes, light attenuation and move speeds. PopcornFX emitters are left as they are and counted. On 163 real models (49 SD and 17 HD game models, 97 imported into corpus maps), the vertices posed at the start, middle and end of each sequence came out as the original ones times 2.5, or plus the offset, to within 1e-13, and every result wrote and read back (#235).
- `wc3files model-normals` and `wc3files.mdl.normals.recompute_normals`: vertex normals from the faces, each triangle weighted by its area. `--weld` joins the vertices at one position whose normals are within `--angle` degrees, so a UV seam is smooth and a sharper edge stays hard. Without it, the normals are a median of 1.0 degrees from the stored ones of 49 SD game models and 1.6 degrees from those of 22 HD models, 87% and 89% of them within 15 degrees; welding at 60 degrees gives 2.7 and 2.6 degrees (#237).
- `wc3files model-remove-unused` and `wc3files.mdl.cleanup.remove_unused`: the materials no geoset or ribbon emitter uses removed, then the textures and texture animations no kept layer or particle emitter uses, with the references renumbered. A layer's texture id, flipbook frames and, from version 1100 on, texture list all count. None of the 418 game models sampled has anything to remove; of 300 models imported into corpus maps, 48 lost 28 materials, 64 textures and 9 texture animations, and every geoset, ribbon and particle emitter kept the same textures, frames and texture animations (#232).
- `wc3files model-team-glow` and `wc3files.mdl.teamglow.remove_team_glow`: the layers that show team glow made transparent, or removed with `--mode remove`, together with the geosets that drew only the glow and what nothing uses any more; later geosets and geoset animations are renumbered. Over 171 models with team glow (34 SD game models, 137 corpus imports), neither way left any glow drawn, and every other geoset kept its vertices and geoset animation (#240).
- `wc3files model-texture-paths` and `wc3files.mdl.paths.rewrite_texture_paths`: texture paths rewritten by whole path (`--replace`), start (`--prefix`) or regular expression (`--pattern`), in MDX and MDL, the rules tried in the order given. Paths compare without regard to case or to `\` against `/`; each change is printed, and a path too long for its 260-byte field is reported, not written. On 718 real models, the result was byte for byte the model with only the expected paths changed: 2,802 paths for `.blp` to `.dds`, and 8 for removing the `war3mapImported\` prefix (#241).
- `wc3files model-textures` and `wc3files.mdl.texture_list`: the textures of models, folders or maps, each with the models that use it. For a map, also the imported images no model uses and no other file names, those that only other files name (a path counts with any extension or none, as maps and scripts write them), and the textures neither in the map nor, with `--game`, in the installed game, where an image counts under any image extension. On 116 corpus maps, 7,337 of 15,360 imported images were found unused, 5,160 named only by other files, and 526 model textures missing from both map and game (#242).
- `wc3files model-keyframes` and `wc3files.mdl.keyframes.remove_redundant_keys`: keyframes removed when the track gives the same values without them: a repeated step, a key its neighbours interpolate to (a rotation by slerp), or the middle of a flat stretch of a curve. Sequence and global sequence ends always stay. `--tolerance` lets values move by that much, never more in total; `--dead` also removes keys no sequence plays, which is not the default since it rests on a reading of sequences not checked in the game. Sampling every track at every 10 ms of each sequence before and after, nothing moved by more than 2e-6 at tolerance 0; that removed 1,210 of 45,900 keys on 50 corpus imports, 77 of 19,606 on 35 SD game models and 376 of 383,820 on 36 HD ones (#230).
- `wc3files model-linear` and `wc3files.mdl.keyframes.linearize`: Hermite and Bezier tracks turned into linear ones, their tangents dropped (lossy). `--tolerance` adds keys where the line moves away from the curve by more than that, on the stretches a sequence plays; the MDX size before and after is printed. Without a tolerance, 50 corpus imports went from 4,679,255 to 4,336,183 bytes and 35 SD game models from 3,750,186 to 3,240,666; the game's HD models have no curved tracks. At a tolerance of 0.01, every played frame stayed within 0.01 of the curve, but the added keys made the files larger (#231).
- `wc3files model-remove-sequences` and `wc3files.mdl.sequences.remove_sequences`: sequences removed by name, with their geoset extents, the keys no kept sequence plays and their event frames; names not found are reported. `--pack` moves the kept sequences up in time, keeping the smallest gap the model had. Removing every other sequence of 41 corpus imports and 25 SD game models removed 27,709 keys and event frames, and every kept sequence played the same on every track, packed or not (#238).
- `wc3files model-transfer-sequences` and `wc3files.mdl.sequences.transfer_sequences`: sequences copied from one model into others, after each target's last sequence, matching nodes and event objects by name (the nth of a repeated name with the nth). Keys are made fit for the target track's interpolation, exactly from linear to Hermite or Bezier; the source nodes with no match, the conversions that lose shape and the geoset animations not copied are reported. Stripping 66 real models to their first sequence and copying the 366 others back, every copied sequence played like the original on every track (#239).
- `wc3files model-obj` and `wc3files.mdl.obj`: Wavefront OBJ and MTL to a static MDX or MDL (a geoset per group and material, polygons as triangles, normals from the faces where missing, textures as `.blp`, a root bone, a Stand sequence and extents), and a model to OBJ and MTL at a frame of a sequence. `--up y` turns a Y-up file upright. The Stanford bunny and a cow came out with every triangle and the same bounding box, and 95 real models taken to OBJ and back kept every triangle and corner, posed or not (#244).
- `wc3files model-gltf` and `wc3files.mdl.gltf`: glTF 2.0 (`.glb`, or `.gltf` with its buffers) to a version 800 MDX or MDL, with every node as a bone posed by its skinning matrix, animations as sequences and base color textures converted to BLP; and a model to `.glb` with a skin and an animation per sequence, global sequences baked in. What version 800 cannot hold is listed in the docs: bone weights become equal parts of the bones above `--min-weight`, key times whole milliseconds, and morph targets, other textures and vertex colours are left out. On eight Khronos sample models every bone matrix matched the glTF skinning within 1e-4, the vertices within 5.2% of the model's size; 59 real models taken to glTF and back came out within 1e-4 when they have no curved tracks, global sequences or unequal weights (21 of 22) (#245).

### Changed

- `optimize --mdx-squeeze` and `squeeze_mdx` squeeze models of version 900 and later too: the same floats as in version 800, plus the geoset tangents, through the model reader, and only when the model reads and writes back to the bytes it came from. On 265 version 800 corpus models, this way gives the same bytes as the version 800 walker. Every one of 418 game models (version 1800) and 35 newer corpus imports with geometry or bone keys was squeezed, no vertex moving by more than 0.78%; once compressed, the game's SD models went from 7.3 MB to 4.9 MB and its HD models from 169.4 MB to 109.7 MB (#233).

### Fixed

- `wc3files.mdl.animation` interpolates Hermite and Bezier rotations by squad, as model viewers do, rather than as a cubic on the quaternion's components, which swung far from both keys when they have opposite signs; a frame on a key takes that key's value on a step track; and a rotation is made unit length before use, as squeezed models keep quaternions a little off it. Extents recomputed from the game's SD models now match theirs more often (#245).
- MDX layers of version 1100 and later read their texture list: `layer['textures']` holds each texture's id, slot and optional `KMTF` track, and `layer['hd']` the flag before it. The reader kept these bytes raw, so the five other textures of an HD layer, and the layer's own tracks after them, were out of reach: layer tracks are now read on 524 of 1,161 such layers, against 117 before. All 718 models sampled (game SD and HD models, corpus imports, versions 800 to 1800) still write back byte for byte. Data read before keeps its `unknown_v1100` bytes and still writes. MDL does not write the list yet (#302) (#301).

## [0.7.0] - 2026-11-26

### Added

- `wc3files pathing-texture` and `wc3files.wpm.texture`: pathing textures read as cells of no-walk, no-fly and no-build flags, written back, made from rows of digits, or made from a model's footprint (`from-model`, snapped to 2x2 blocks, with an optional unbuildable border). Red, green and blue block walking, flying and building, as the game's own textures show; all 209 of them read and write back with the same cells. Against the 196 game buildings with a model and a texture, the footprint overlaps the game's texture with a median intersection over union of 0.80 (#262).
- `wc3files map-description` and `wc3files.w3x.description`: a map description from the map's own data, in BBCode for the Hive Workshop or in Markdown: name, author, description, players and forces, playable size, tileset, and for a melee map its start locations and gold mines, each mine at a start location or an expansion. `--template` takes a `string.Template` of one's own, and `--print-template` gives the default. A map is melee when its map info says so or it places gold mines and start locations, since few melee maps set the flag. All 342 readable corpus maps render in both formats, 98 of them as melee with 1,757 gold mines; one map keeps two `TRIGSTR_` references its `war3map.wts` does not have (#253).
- `wc3files campaign-split` and `wc3files.w3x.campaign.split_campaign`: each map of a `.w3n` campaign as a standalone map, with the campaign's object data merged under the map's own, its gameplay constants and interface where the map has none, its imported files and the strings these use. Files are named after the mission index and campaign button, and the title becomes the map's name. `transfer` takes `prefer='target'` for this. Checked on a campaign built around three missions of the game's own Human campaign, with a custom map's object data and strings as the campaign's: every campaign object present, the missions' own objects unchanged, every string resolving to the campaign's text, and no new error in `run-map`. No real `.w3n` was available to check against (#249).
- `wc3files transfer` and `wc3files.w3x.transfer.transfer`: the object data, triggers and script, game interface, gameplay constants, imports and sounds of one map copied into any number of target maps, which keep their terrain, placed objects, regions, cameras and map info. The script is merged by function: the target keeps what the World Editor writes from its map info and placed objects, and the source brings its triggers, custom code and `main` after `InitBlizzard()`. A placement variable the source's triggers name and the target lacks is declared null and reported. The source's strings go into the target's `war3map.wts`, renumbered where the number is taken. A JASS source is translated for a Lua target. 44 corpus transfers into JASS melee maps and 8 into a Lua map each ran 10 s of game time in `run-map` with no more errors than the source alone (#218).
- `wc3files objects-merge` and `wc3files.w3o.merge.merge_objects`: the objects of one object file, or of all the object data of a map, put into another's. A custom rawcode both use and an edit of a default object both change take a policy each: `remap` (a free rawcode that no side and no default object uses; a conflicting edit becomes a new custom object based on the default), `first` or `second`. The second side's ability, build, train and other lists follow its remapped rawcodes, and the old-to-new table is returned, or written with `--table`. Merging the object data of each of 303 corpus maps with the next under each policy gave 184,504 conflicts and, with `remap`, 274,801 list items rewritten, no rawcode collision, and files that read back with the same objects (#217).
- `wc3files doodads-merge` and `wc3files.doo.merge.merge_doodads`: the doodads and destructables of several maps or `war3map.doo` files in one file, in the newest format among them. The first input keeps its editor ids and the others are numbered on from its highest, or every record from 0 with `--renumber-all`. Reported: a renumbered destructable that its map's script or triggers name as `gg_dest_`, a record pointing to another map's item table, duplicate special doodads, and ids the first input itself repeats. Merging each of 341 corpus maps with the next and reading the result back gave no new duplicate id; 1,746 `gg_dest_` references on 88 pairs would break and are reported (#219).
- `wc3files import-move --fix-dots` and `wc3files.w3x.imports.multi_dot_moves`: files whose name has more than one dot, which patch 3.0 is described as not loading, renamed with each dot but the last turned into an underscore, and their references rewritten; `--report-only` lists them. The check is not on by default, since the behavior has not been confirmed on the current patch. 94 of 342 corpus maps have such names; on them, 2,588 files were renamed and 2,467 references and 2,418 `war3map.imp` entries rewritten, with nothing left unresolved (#213).
- `wc3files import-move` and `wc3files.w3x.imports.move_imports`: imported files renamed with `--move OLD NEW`, or out of `war3mapImported\` with `--strip-prefix`, and every reference to them rewritten: object data, map info, sounds, GUI and custom-text triggers, the script, FDF and other text files, MDX paths and `war3map.imp`. A model matches as `.mdl` or `.mdx` and its `_portrait` model moves with it. What cannot be updated is reported, such as a script string ending in the old folder. Stripping the prefix on 37 corpus maps moved 2,710 files and rewrote 3,034 references and 1,941 `war3map.imp` entries; the object data matched an independent rewrite of its JSON on every map (#214).
- `wc3files import-folder` and `wc3files.w3x.imports.import_folder`: every file of a folder tree added to a map at the same relative path, or under `--prefix`, and listed in `war3map.imp`. A member the map has is replaced, or kept with `--keep-existing`; files named as map data are skipped. The MCP `import_file` tool takes its flags byte from the same module (#215).
- `wc3files textconv`: a map file as text for `git diff`, with a `.gitattributes` and `git config` recipe in `docs/cli.md`. Files `dump` reads print as its JSON with one record per line (a doodad, a tile, a region), so 78,461 lines of `war3map.doo` on one corpus map become 3,575; text files print as they are, an MDX model as MDL, and other files as their size and SHA-256. Over 40 corpus maps, all 6,937 files gave the same text on two reads (#254).
- `wc3files replay-info` and `wc3files.w3g.summary.summarize`: what happened in a replay. Players come from the game's slot table, with team, observers, colour by its World Editor name, race and handicap; the summary adds who left and when, the chat with timestamps and mode, and the W3MMD results. The MCP `replay_info` tool returns the same summary, with a `chat` switch and `max_chars`; its players now give `player_id` and slot rather than `id`. The slot layout fits all 294 local replays that carry the record, and all 296 summarize, with 44,673 chat messages and 3,010 leaves (#263).
- `wc3files map-strings` and `wc3files.w3x.strings`: the strings of a map by object, field and level, from `war3map.wts` or inline in the object data, with the other files that use `war3map.wts` and the strings nothing uses. `--check` reports `TRIGSTR_` references with no string, `<A000,DataA1>` references that do not resolve, and short tooltips naming another object of the map (#252).

### Fixed

- Ability, doodad and upgrade levels are named as Blizzard's tables name them: level n of a field is `{name}{n}` (`DataA1` for level 1) and a field without levels has no suffix (`Name`). `to_slk`, `object_data`, the SLK export and the override tables named every level one too high (`DataA2`, `Name1`). Over 342 corpus maps, fields with levels are stored from level 1 and fields without levels at level 0. The override tables now drop an upgrade or ability value that only restates the base default, as they already did for units: on 60 corpus maps, the 1,746 values affected all equal their default (#277).
- `optimize --computed-text` resolves a reference to a level the map added to an ability and left unset, such as `<A000,Dur5>` on a five-level copy of a three-level ability: the level takes the last value of the game's table, as w3x2lni fills it. Such references were left in the text (#252).
- Items take their name, tooltip, icon and the other shared fields from the unit metadata rows marked `useItem`, so `object_data` names an item's name `Name` rather than `unam` (#283).

## [0.6.4] - 2026-11-25

### Added

- MCP `edit_script` and `import_file` (with `--allow-write`). `edit_script` adds JASS or Lua code before `main`, with an optional call at the top of `main` (after its locals in JASS), merges a JASS `globals` block into the script's own, or replaces the script; the result is parsed and checked, and refused on an issue the original did not have. `import_file` adds or replaces a file and lists it in war3map.imp; map data and the script are refused. On the 58 readable maps of a 90-map corpus sample, 58 script edits (55 JASS, 3 Lua) kept every original line exactly and in order, and 58 imports left every other file unchanged (#275).

- MCP `edit_records` (with `--allow-write`): add, change or delete regions and cameras, and add or change strings. A region or camera the script or GUI triggers use cannot be deleted or renamed; new cameras start as the game's default camera; a new string comes back with its `TRIGSTR_` reference. On the 74 readable maps of a 100-map corpus sample, 222 edits (a region, a camera and a string on each) left every existing record and every other file unchanged (#274).

- MCP `edit_placed` (with `--allow-write`): place, move, change or delete placed units or doodads, picked by index, type id or editor number. Type ids are checked against the game's and the map's objects, positions against the terrain bounds, and new records get the next free editor number. On the 92 readable maps of a 120-map corpus sample, 86 unit edits (one unit added, one moved) and 90 doodad deletions each left every other record and every other file unchanged (#273).

- `wc3files-mcp --allow-write` registers edit tools, starting with `edit_objects`: create custom objects from standard ones, change or reset their fields by the names `object_data` shows or by field id, or delete them. An edit writes a new map (`<name>.edited.w3x` unless `out` says otherwise), reads it back and checks it; every other file is copied unchanged. Without the option the server stays read-only and lists no edit tool. On the 84 readable maps of a 120-map corpus sample, 441 edits (a new object in each object file a map has, all seven types) each left every existing object and every other file unchanged (#272).

- Three MCP tools that read the rest of a map: `map_overview` (what a map holds, in one call), `placed_objects` (placed units and doodads, filtered by type or owner, with facing in degrees) and `map_records` (regions, cameras, sounds, imports, strings, or a terrain summary). Each has an output schema; on 1,602 results from 250 corpus maps, each validates and comes back unchanged (#271).

## [0.6.3] - 2026-11-25

### Changed

- Every `wc3files-mcp` tool publishes an output schema, one TypedDict per result in `wc3files.mcp.results`, so a client knows what comes back. On 2,523 results from real maps, models and replays, each validates and the structured result equals what the tool function returns. `object_data`'s `object_type` is an enum, and the descriptions are shorter now that the schemas carry the fields (#268).
- `model_info` gives each sequence with its start and end frame, and each texture as its path and replaceable id (#268).

### Fixed

- `model_info` returned null for every texture: the model reader keys a texture `path`, and the tool read `image` (#268).

## [0.6.2] - 2026-11-25

### Added

- `wc3files-mcp` tools declare that they only read (the MCP annotations `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint`), have a title, describe every parameter in their input schema, and say when to use each tool instead of another. glama.ai rated the old definitions 2.8 of 5. The repository has a `glama.json` naming its maintainer (#265).
- The README links to the server's page on glama.ai, an MCP directory, with its score badge. glama builds and checks the server on each release (#200).
- A `Dockerfile` that runs `wc3files-mcp` on a mounted `/data` folder, with a `.dockerignore` that only lets the package sources into the build. CI builds the image and checks that the server answers an MCP `initialize` request. See `docs/mcp.md` (#198).

## [0.6.1] - 2026-11-25

### Added

- `server.json` describes `wc3files-mcp` for the official MCP Registry as `io.github.Bekhouche/wc3files`, started with `uvx --from "wc3files[mcp]" wc3files-mcp --root FOLDER`, and the README carries the `mcp-name:` line the registry checks on PyPI. `docs/mcp.md` covers both (#195).

## [0.6.0] - 2026-11-25

### Added

- `wc3files.ui`: in-game UI frames for 1.31 and later, described as a layout (JSON or Python) and written as the JASS or Lua that creates them with the frame natives, as an .fdf, or as a custom-text trigger merged into a map's `war3map.wtg` and `war3map.wct` for World Editor users, classic or Reforged. An .fdf reads back into a layout. CLI `ui-build`; see `docs/ui.md` (#172).
- An MCP server, `wc3files-mcp` (`pip install "wc3files[mcp]"`), so an AI assistant such as Claude can read maps, models and replays: `list_files`, `map_info`, `list_members`, `read_member`, `object_data`, `trigger_text`, `check_script`, `model_info` and `replay_info`. Every tool only reads, opens files under the `--root` folders only, and refuses protected maps. The tools are plain functions in `wc3files.mcp.tools`. See `docs/mcp.md` (#179).
- Every map, model, table and script file class has `get_data()` and `import_data(data)`, which returns the handler. `RegionFile` and `PreviewIconsFile` had neither, eleven classes had no `import_data()`, and seven had only `export_data()`, which stays as the same method. The CLI no longer sets their private `_data` (#174).
- Every map sub-file class has `to_bytes()`, so a parsed member can be changed and put back into `W3xFile.files` without a temporary file; `write()` saves what `to_bytes()` returns. Only `MapDoodadsFile` had it (#173).

### Changed

- MDX keyframe tracks are read in one pass per track, with one unpack per key. Over every fifth corpus model (1,144 models, 164 MB, read from memory), MDX reads at 46.5 MB/s, up from 25.6, with the same data for all 5,716 models (#176).
- MDL vectors, float lists and keyframe tracks are read from the token list directly, and the tokenizer drops comments in its regular expression. Over every fifth corpus model as MDL (1,142 models, 459 MB), MDL reads at 9.4 MB/s, up from 5.9, with the same data for every model (#177).

### Removed

- The empty `wc3files.gui` package; `wc3files.ui` takes its place (#172).
- Python 3.9, which reached its end of life in October 2025. The package needs 3.10 or newer, and CI tests 3.10 as the oldest version on Linux, Windows and macOS (#178).

### Fixed

- A replay with a damaged data block raises `ReplayBlockError`, a `ValueError`, with the zlib error as its cause, instead of `zlib.error`. A block cut short raises `TruncatedDataError` instead of a plain `EOFError` (#166).
- `JassLexError`, `JassParseError`, `LuaLexError` and `LuaParseError` are `ValueError` subclasses, as the other readers' errors are, so `except ValueError` around `ScriptFile.read` catches a script with a syntax error (#167).
- MDL reads `-0` as a negative zero float, so MDX to MDL to MDX keeps the sign of zero. On every tenth corpus model (571), MDL to MDL is a fixed point for 523, up from 441, and MDX to MDL to MDX is exact for 117, up from 102 (#168).
- The CLI help and error messages no longer send users to TODO.md, which is not in the sdist or the wheel. They say what is needed, or link to `docs/cli.md`, which has new sections on checking object data, override tables and base game data. A `war3map.w3i` version 39 error asks for the map to be reported. The `img-to-png` summary lists every image format it reads (#169).
- `docs/lni.md` gives current Lni round-trip figures. It said 2 object files were refused, which #30 fixed in 0.3.0 (#175).
- The build prints no warnings: the deprecated license classifier is gone (`license='MIT'` already said it), and the data folders are listed as namespace packages. CI fails the package job on a build warning (#171).
- Python 3.14 is declared and tested: CI runs it on Linux, Windows and macOS as the newest version, and builds the package with it (#170).

## [0.5.0] - 2026-11-24

### Added

- This changelog (#133).
- `W3xFile.read(members=...)` and `read_map(members=...)` read only the named files, so the rest of the map is not decompressed (#139).

### Changed

- `W3xFile()` and `MpqFiles()` no longer read the 332,532 reference names when they are created. A DEEP read loads them once, on first use, and probes them in sorted order. Reading 30 small corpus maps goes from 5.2 to 1.9 s (#135).
- `BinaryFile` no longer asks the file for its position before every read, and reads a file of up to 64 MB into memory. Reading w3e, wtg and object data files is about twice as fast: w3e goes from 1.0 to 2.7 MB/s, wtg from 3.3 to 6.7 MB/s (#136).
- `TerrainFile` reads and writes the tile grid in one pass. war3map.w3e reads at 10.3 MB/s, up from 2.7, and writes at 11.0 MB/s, up from 3.2 (#137).
- The JASS lexer walks the source with `finditer` and takes columns from the line start. It gives the same tokens on all 703 corpus scripts, 32% faster, and `ScriptFile.read` parses JASS at 3.1 MB/s, up from 2.7 (#138).
- `jass --check`, `trigger-text`, `script-to-triggers` and `run-map` read only the members they use from a map, and `script-to-triggers` reads it once instead of twice. On a 350 MB map, `script-to-triggers` goes from 6.0 to 2.0 s (#139).
- The MPQ modules and `W3xFile` import their names explicitly instead of with `from .data import *`, a dead branch and unused parameters are gone, and CI runs pyflakes over `src/` (#148).
- The package no longer prints log lines unless the application enables them with `logger.enable('wc3files')`. Reading an SLK table printed an INFO line to stderr. The command line still shows warnings (#145).

### Fixed

- A truncated or damaged `war3map.doo` raises `TruncatedDataError` instead of `struct.error` or `IndexError` (#140).
- A truncated MDX model raises `TruncatedDataError`, which is still an `EOFError`, instead of a plain `EOFError` (#141).
- A BLP or DDS image cut short raises the new `BlpTruncatedError` or `DdsTruncatedError` instead of `struct.error`. Each is both the format's error and a `TruncatedDataError` (#142).
- A damaged zlib or bzip2 MPQ sector raises `MpqCompressionError`, with the zlib or bzip2 error as its cause, instead of `zlib.error` or `OSError` (#143).
- An unknown GUI function in `war3map.wtg` raises the new `TriggerDataError`, which is both a `KeyError` and a `ValueError`, and an invalid function type raises `ValueError` instead of `KeyError` (#144).
- `RegionFile` and `PreviewIconsFile` read bytes, as the other readers do. `ObjectFile` uses `object_type=` for a path whose extension is not an object type, and its error says when `object_type=` is needed (#146).
- The README no longer counts 28 subcommands (there are 35), and `docs/cli.md` shows `gamedata-extract`, `trigger-ui` and `optimize` (#147).
- On Windows, a command whose output goes to a file or a pipe writes UTF-8, instead of stopping with a `charmap` codec error on text outside the code page. `PYTHONIOENCODING` is respected (#153).

## [0.4.0] - 2026-11-23

### Added

- `python -m wc3files` runs the CLI (#73).
- `jass --check`, `trigger-text` and `script-to-triggers` take a map as well as a single file (#74).
- `dump` and `build` take the format from the file name when it is not given, and `units` names `war3mapUnits.doo` (#76).
- DDS reads BC4 and BC5 (ATI1, ATI2), also behind a DX10 header (#88).
- MDL reads and writes lights: their type, static values and animated tracks (#86).
- `W3iFile.map_flag_names()` names every map flag, and `encode_flags` keeps bits it has no name for (#67).
- The six unknown `war3map.w3s` dialogue fields have names: `listener_name`, `asset_flags`, `speaker_unit`, `animation_label`, `animation_group`, `animation_set_path`. The old names still read (#66).
- GUI triggers rebuilt from a script tell `SetPlayerUnitMaxAllowed` from `SetPlayerTechMaxAllowedSwap` by the map's object data (`object_ids=`) (#81).
- A `progress=` parameter on map reads. Progress bars need the new `progress` extra, `pip install wc3files[progress]` (#64).
- The README credits the projects wc3files bundles data from or follows (#89, #91).
- The test suite runs in GitHub Actions on Linux, Windows and macOS, Python 3.9 to 3.13, and the package is built and installed there (#72).

### Changed

- A map whose `(listfile)` is missing or leaves files out counts as protected. `unpack`, `lni`, `slk`, `obj`, `optimize` and `run-map` refuse it with `ProtectedMapError`, instead of writing an almost empty result and exiting with 0 (#62, #63).
- tqdm is no longer a dependency, and the library prints no progress unless asked (#64).
- MPQ decryption goes from 4.0 to 6.6 MB/s and encryption from 4.7 to 6.7 MB/s (#78). PKWare DCL decompression goes from 1.9 to 4.9 MB/s (#79).
- The Lua printer adds only the parentheses it needs (#69).
- The JASS and Lua parsers take 200 nested levels (#70).
- MDL writes floats with as many digits as float32 needs. MDX to MDL to MDX gives the original bytes for 927 of 5,712 corpus models, up from 154 (#84). The rest is #110.
- The README and the docs no longer describe reading protected maps (#122, #130).

### Fixed

- `W3xFile.encrypt` works again, and `W3xFile` uses the one shared MPQ cipher (#77).
- A `(listfile)` that names only itself no longer makes a protected map read as empty (#62).
- `MpqWriter` no longer adds an empty sector to a file whose size is a multiple of the sector size (#68).
- A doodad or unit added by code gets the file's skin layout, so the file is not written with mixed records (#65).
- `lni` writes a trigger name that is not valid UTF-8 (#83).
- The MDL tokenizer no longer cuts a quoted string at `//` (#87).
- MDL reads back the `nan` and `inf` it writes (#85).
- Rebuilt `TriggerRegisterVariableEvent` passes the variable, not its name as a string (#80).
- `run-map --max-steps` stops the run (#75).
- The README examples use classes and methods that exist, and run as tests (#71).
- The bundled game files keep their CRLF line endings on Linux and macOS (#124).
- Deep Lua recursion no longer crashes Python 3.9 and 3.10 on Windows (#126), and nested Lua coroutines start there (#128).

## [0.3.0] - 2026-11-22

A fixes release: 21 issues found in a full check of the package against 1,025 real maps.

### Added

- `wc3files --version` (#37).
- Every reader and writer accepts `pathlib.Path` and other path-like objects (#18).

### Changed

- MPQ hash lookups probe from the hash index, as the game does, instead of scanning the whole table. A lookup on the largest protected table in the corpus goes from 3.0 ms to 0.045 ms (#34).
- `wc3files.files`, `wc3files.data`, `wc3files.mpq` and `wc3files.utils` are the real packages again, and each lists its exports in `__all__` (#35).
- A truncated file raises `TruncatedDataError` instead of being read with zeros (#19).

### Fixed

- On Linux and macOS, `export()` writes members into folders instead of files named with a backslash (#21), and encrypted members stored in folders are read (#22).
- Member names with characters above U+00FF are hashed as their bytes instead of raising `KeyError` (#23).
- A DEEP read skips a block it cannot read instead of stopping (#39).
- Empty files are kept on read (#33).
- `build_from_files()` no longer keeps a stale `(attributes)` or `(signature)` (#24).
- Object data: version-3 files without modifications (#26), a field set twice (#27), a field whose levels use different types (#28), an original entry with a non-zero new id (#29) and a table with two entries of the same id (#30) are written back exactly. All 3,706 corpus object files round-trip, up from 3,646.
- A w3i that ends inside its trailing tables keeps the bytes that are left (#31).
- In a WTS file, an empty entry no longer swallows the next one (#25).
- Trigger files that use natives, BJ functions or `OperatorCompare*` conditions missing from TriggerData.txt are read (#32).
- REAL values are little-endian on every platform (#36).

### Security

- Unpacking no longer writes outside the output folder. A member named like `..\..\file`, an absolute path or a drive name is rejected (#20).

## [0.2.0] - 2026-11-22

### Added

- A vJass and Zinc compiler. `//! external` Grimex tools run through a pure-Python Lua interpreter, and `vjass --check` reports each finding at its vJass source line.
- A Lua interpreter that runs a map script without the game. Its 5.3 mode passes 18 files of the Lua 5.3.4 test suite.
- Whole-map modes: Lni (a map as a folder of text files and back), Slk and Obj.
- A map optimizer: removes unused objects and editor-only files, squeezes models and computes text.
- GUI trigger definitions convert to and from YDWE's ui folder.
- Game tables can be read from a profile of another game version.
- JASS and Lua findings carry their line, and Reforged `war3map.lua` is checked against the game API.
- Image conversion, and the missing Grimex tools.
- `ObjectFile.filter`: filter by object type, table and ids (#3).

### Removed

- pandas is no longer a dependency (#5). `import wc3files` goes from about 0.82 s to 0.28 s.
- Dead code reported by pyflakes (#7).

### Fixed

- Every version-39 w3i is read, and misaligned parses are no longer kept.
- Uncompressed MPQ sectors are read, and every MPQ compression method is decoded in Python.
- Any MPQ format version is read as format 0, as the game does, without printing anything (#12).
- MDX rewrites are byte-exact.
- Unfinished replays are read.
- `ObjectTable.filter(remove=...)` drops the listed ids instead of keeping them (#2).
- The MPQ version-4 header fields are defined once, and completely (#4).
- The two SYLK currency format keys no longer raise invalid-escape warnings (#1).
- CLI commands exit with the status they return.

## [0.1.0] - 2026-10-24

First release. A pure-Python reader and writer for Warcraft III's file
formats: MPQ archives and W3X/W3M/W3N maps, the map sub-files, object data
with the game's own field schema, GUI and custom text triggers, JASS and Lua
scripts, MDL/MDX models, BLP/DDS/TGA/BMP/TIFF/PNG images, replays, UI and
effect files, and the CASC storage of an installed game.

[Unreleased]: https://github.com/Bekhouche/wc3files/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/Bekhouche/wc3files/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/Bekhouche/wc3files/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/Bekhouche/wc3files/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/Bekhouche/wc3files/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Bekhouche/wc3files/compare/v0.6.4...v0.7.0
[0.6.4]: https://github.com/Bekhouche/wc3files/compare/v0.6.3...v0.6.4
[0.6.3]: https://github.com/Bekhouche/wc3files/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/Bekhouche/wc3files/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/Bekhouche/wc3files/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Bekhouche/wc3files/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Bekhouche/wc3files/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Bekhouche/wc3files/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Bekhouche/wc3files/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Bekhouche/wc3files/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Bekhouche/wc3files/tree/v0.1.0

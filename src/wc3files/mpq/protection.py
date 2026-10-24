"""
Opt-in MPQ-container "protection": techniques real Warcraft III map
protectors use to make an archive hard for third-party tools (MPQ editors,
listfile scanners, decompilers) to read while the game itself still loads it.
This is the same goal the JASS/Lua identifier obfuscator (`scripts/obfuscator.py`,
`scripts/lua_obfuscator.py`) serves at the script layer - raising the bar
against casual asset/script theft, not security against a determined
attacker (every technique here is a publicly documented trick, not a
secret) - just applied at the MPQ container layer instead.

**Sources** (see TODO.md for the full citations): this package's own
`w3x2lni` reference clone turned out not to be a source of technique detail
at all - its actual archive writing delegates 100% to StormLib via FFI, it
doesn't hand-roll any container-level corruption itself. The real source is
StormLib's own upstream source code comments (its author, Ladislav Zezula,
documents the specific malformed-archive shapes real WC3 protectors produce,
since StormLib has to tolerate them to open protected maps at all) plus
community documentation (hiveworkshop.com, wc3edit.net, StormLib's GitHub
issue tracker).

**Verification status (updated 2026-08-29): all four tiers below have been
confirmed to load and play in a real Warcraft III Reforged client**, via a
sibling project's real release pipeline (build, then host and play the
resulting `.w3x` in-game) - not just this package's own mechanical
read-back/structural checks. This was a real, meaningful open question until
tested: EXPERIMENTAL/AGGRESSIVE's core assumption (Storm.dll tolerating a
duplicate hash-table entry) was inferred from a single paraphrased source
comment, not confirmed against Storm.dll's own source, before this test.
That single real-map test doesn't prove every possible file set/map size
behaves identically (no exhaustive in-game test matrix exists), so treat
this as strong, real evidence rather than an exhaustive guarantee - but it's
a substantially stronger verification status than "matches a documented
technique" alone.

Four levels, each a strict superset of the one before:

- **NONE**: no protection - the plain, standard archive layout this writer
  has always produced.
- **SAFE**: `(listfile)` is omitted entirely (no hash-table entry at all,
  not just empty/garbage content) - real WC3 doesn't need it to load a map
  it already knows the required filenames for (hash-based lookup, not
  listfile-content-based) - and this package's own reader already has a
  fallback path for exactly this case (`mpq/files.py`'s "check known files
  from the bundled reference list" branch), so it's a real, safe technique,
  not a guess. The hash table is also oversized and padded with decoy noise
  entries (random-looking but non-colliding name hashes pointing at a real,
  reused block index) - inert by construction: a decoy can never match a
  real lookup's name hash, so it cannot change how any real file resolves,
  regardless of whatever Storm.dll's exact tolerance behavior turns out to
  be. This tier carries no risk of breaking a real load.
- **EXPERIMENTAL**: adds one duplicate/decoy hash-table entry per real file -
  the "two hash entries with the same name, where only the second one is
  valid" technique StormLib's own source has to special-case for real WC3
  maps (a decoy sharing the real file's exact name hash, inserted earlier in
  the same open-addressing probe chain, pointing at a block-table entry
  deliberately marked as not existing).
- **AGGRESSIVE**: the same trick as EXPERIMENTAL, but with 3 decoys per file
  instead of 1 (4 hash-table slots sharing one name total) - matching
  community documentation of more heavily "collision-padded" real protected
  maps. Deliberately **not** included: the "BOBA protector" technique
  (relocating the block table to before the MPQ header, so its offset field
  is a wrapped/negative value) that a StormLib source comment also mentions.
  Reproducing that correctly requires exact 32-bit register-overflow
  semantics this package has no way to verify without a real client or
  StormLib's own source for the read side - implementing a guess here risks
  producing an archive that fails to load even in the real game, the one
  outcome a "protection" feature must never risk. Disclosed as a known,
  real, but deliberately unimplemented technique - see TODO.md.

A separate, real limitation remains regardless of in-game confirmation: this
package's own reader (`MpqHashTable.get_hash_table_entry`) resolves by plain
name-match and doesn't cross-reference block-table validity, so it's fooled
by EXPERIMENTAL/AGGRESSIVE's own decoys just like the third-party tools this
is meant to defeat - a real file becomes unresolvable via `MpqFile`/`W3xFile`
too at those tiers. That's unaffected by the in-game confirmation above (a
different reader entirely) - see the tests for exactly what this means.
"""

import random

from .data import MPQHashTableEntry, MPQBlockTableEntry, MPQFileDataType, MPQHashType, MpqProtectionLevel

_DECOYS_PER_FILE = {
     MpqProtectionLevel.NONE: 0,
     MpqProtectionLevel.SAFE: 0,
     MpqProtectionLevel.EXPERIMENTAL: 1,
     MpqProtectionLevel.AGGRESSIVE: 3,
}

_EMPTY_HASH_ENTRY = {
     MPQHashTableEntry.FILE_PATH_HASH_A.value: 0xFFFFFFFF,
     MPQHashTableEntry.FILE_PATH_HASH_B.value: 0xFFFFFFFF,
     MPQHashTableEntry.LANGUAGE.value: 0xFFFF,
     MPQHashTableEntry.PLATFORM.value: 0xFFFF,
     MPQHashTableEntry.FILE_BLOCK_INDEX.value: 0xFFFFFFFF,
}


def should_include_listfile(level):
     """SAFE and above omit (listfile) entirely - real WC3 doesn't need it
     (see this module's docstring)."""
     return level == MpqProtectionLevel.NONE


def decoys_per_file(level):
     """How many duplicate/decoy hash-table entries to insert per real file,
     ahead of its real entry in the same probe chain (0 below EXPERIMENTAL)."""
     return _DECOYS_PER_FILE[level]


def invalid_block_entry():
     """A block-table row that deliberately does not describe a real file
     (no MPQ_FILE_EXISTS flag) - every decoy hash entry points here."""
     return {
          MPQBlockTableEntry.BLOCK_OFFSET.value: 0,
          MPQBlockTableEntry.BLOCK_SIZE.value: 0,
          MPQBlockTableEntry.FILE_SIZE.value: 0,
          MPQBlockTableEntry.FLAGS.value: 0,
     }


def format_version_for(level, rng):
     """NONE keeps the standard, correct format-version value (0 - classic
     MPQ v1). SAFE and above use a nonstandard value instead: a real StormLib
     source comment confirms Warcraft III's own Storm.dll ignores this field
     entirely, while some third-party tools validate it strictly and reject
     anything unrecognized."""
     if level == MpqProtectionLevel.NONE:
          return 0
     return rng.randint(4, 0xFFFF)


def build_hash_table(named_block_indices, mpq_cipher, level=MpqProtectionLevel.NONE,
                      invalid_block_index=None, rng=None):
     """Builds the MPQ hash table for `named_block_indices` (a list of
     `(name, block_index)` pairs - one per real, named file, in the order
     their block-table rows were created). At `level=NONE` this is exactly
     the classic construction (oversized-by-2x, one entry per file, no
     decoys) - every level above it additionally inserts `decoys_per_file(level)`
     duplicate-name decoy entries per file (see this module's docstring) and,
     for SAFE and above, pads the table with inert random-noise entries that
     can never match a real lookup.
     """
     rng = rng or random.Random()
     n_decoys = decoys_per_file(level)
     if n_decoys and invalid_block_index is None:
          raise ValueError('invalid_block_index is required when decoys_per_file(level) > 0')

     real_count = len(named_block_indices)
     min_size = max(4, real_count * 2)
     if level != MpqProtectionLevel.NONE:
          # Oversized further so decoy noise padding (below) has real room to
          # work with without crowding real files' own probe chains.
          min_size = max(min_size, real_count * 4)
     size = 4
     while size < min_size:
          size *= 2

     table = [dict(_EMPTY_HASH_ENTRY) for _ in range(size)]
     real_hashes = set()

     for name, block_index in named_block_indices:
          hash_a = mpq_cipher.hash(name, MPQHashType.MPQ_HASH_NAME_A.value)
          hash_b = mpq_cipher.hash(name, MPQHashType.MPQ_HASH_NAME_B.value)
          real_hashes.add((hash_a, hash_b))
          home_slot = mpq_cipher.hash(name, MPQHashType.MPQ_HASH_TABLE_OFFSET.value) & (size - 1)

          for _ in range(n_decoys):
               _insert(table, home_slot, size, hash_a, hash_b, invalid_block_index)
          _insert(table, home_slot, size, hash_a, hash_b, block_index)

     if level != MpqProtectionLevel.NONE:
          _pad_with_decoy_noise(table, size, real_hashes,
                                 [index for _, index in named_block_indices], rng)

     return table


def _insert(table, home_slot, size, hash_a, hash_b, block_index):
     """Open-addressing linear probe from `home_slot`, same algorithm real
     MPQ hash-table lookups use - lands on the first still-empty slot."""
     slot = home_slot
     while table[slot][MPQHashTableEntry.FILE_BLOCK_INDEX.value] != 0xFFFFFFFF:
          slot = (slot + 1) % size
     table[slot] = {
          MPQHashTableEntry.FILE_PATH_HASH_A.value: hash_a,
          MPQHashTableEntry.FILE_PATH_HASH_B.value: hash_b,
          MPQHashTableEntry.LANGUAGE.value: 0,
          MPQHashTableEntry.PLATFORM.value: 0,
          MPQHashTableEntry.FILE_BLOCK_INDEX.value: block_index,
     }
     return slot


def _pad_with_decoy_noise(table, size, real_hashes, real_block_indices, rng):
     """Fills a bounded fraction of the still-empty slots with inert noise
     entries: random name hashes (guaranteed to never collide with a real
     file's, so they can never be returned for a real lookup) pointing at a
     randomly-reused *real* block index (no extra block-table data needed).
     Purely inflates what a naive listfile-scanning tool thinks the archive
     contains - never touches how any real file resolves."""
     empty_slots = [i for i, entry in enumerate(table)
                    if entry[MPQHashTableEntry.FILE_BLOCK_INDEX.value] == 0xFFFFFFFF]
     # Leave at least half genuinely empty so open-addressing probe
     # termination (an empty slot means "definitely not present") still
     # behaves normally for names that truly don't exist in the archive.
     budget = min(len(empty_slots) // 2, max(4, len(real_block_indices) * 2))

     for slot in rng.sample(empty_slots, budget) if budget else ():
          while True:
               hash_a = rng.getrandbits(32)
               hash_b = rng.getrandbits(32)
               if (hash_a, hash_b) not in real_hashes and (hash_a, hash_b) != (0xFFFFFFFF, 0xFFFFFFFF):
                    break
          table[slot] = {
               MPQHashTableEntry.FILE_PATH_HASH_A.value: hash_a,
               MPQHashTableEntry.FILE_PATH_HASH_B.value: hash_b,
               MPQHashTableEntry.LANGUAGE.value: 0,
               MPQHashTableEntry.PLATFORM.value: 0,
               MPQHashTableEntry.FILE_BLOCK_INDEX.value: rng.choice(real_block_indices),
          }

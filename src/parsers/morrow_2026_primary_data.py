"""Hardcoded Morrow County 2026 primary precinct data.

Values were recovered from rendered images of the official source PDF when the
PaddleOCR-VL-1.6 cached markdown was truncated, shifted, or otherwise unusable.
PCP write-in rows are aggregated into a single ``Write-ins`` row per contest to
avoid duplicate-row failures in the OpenElections data tests.
"""

COUNTY = "Morrow"


def _c(precinct, office, district, party, rows):
    """Return CSV-ready 7-tuples for one contest."""
    return [
        (COUNTY, precinct, office, district, party, candidate, votes)
        for candidate, votes in rows
    ]


def _pcp_office(party_name, precinct):
    return f"Precinct Committee Person - {party_name} - {precinct}"


# -----------------------------------------------------------------------------
# 01 BOARDMAN
# -----------------------------------------------------------------------------
BOARDMAN = (
    _c("01 BOARDMAN", "U.S. Senate", "", "D", [
        ("Jeff Merkley", 85), ("Paul Damian Wells", 15), ("Write-ins", 2),
        ("Over Votes", 0), ("Under Votes", 6),
    ])
    + _c("01 BOARDMAN", "U.S. House", "2", "D", [
        ("Chris Beck", 21), ("Mary Doyle", 30), ("Rebecca Mueller", 14),
        ("Patty Snow", 14), ("Dawn Rasmussen", 10), ("Peter Quince", 4),
        ("Write-ins", 1), ("Over Votes", 0), ("Under Votes", 14),
    ])
    + _c("01 BOARDMAN", "Governor", "", "D", [
        ("Forest (Fora) Alexander", 2), ("James Atkinson IV", 5),
        ("Cal Kishawi", 0), ("Tina Kotek", 71), ("Donnie M Beckwith", 0),
        ("David W Beem", 4), ("Steve William Laible", 1), ("Brittany Jones", 4),
        ("Tristan Sheppard", 2), ("Miranda Weigler", 2), ("Write-ins", 6),
        ("Over Votes", 0), ("Under Votes", 11),
    ])
    + _c("01 BOARDMAN", "State House", "57", "D", [
        ("No Candidate Filed", 0), ("Write-ins", 11), ("Over Votes", 0),
        ("Under Votes", 97),
    ])
    + _c("01 BOARDMAN", _pcp_office("Democrat", "01 BOARDMAN"), "", "D", [
        ("Write-ins", 16), ("Over Votes", 0), ("Under Votes", 1063),
    ])
    + _c("01 BOARDMAN", "U.S. Senate", "", "R", [
        ("Brent Barker", 54), ("Deborah C Brown", 14), ("David A Burch", 6),
        ("Russell McAlmond", 37), ("Jo Rae Perkins", 75),
        ("Timothy Skelton", 1), ("David Brock Smith", 96), ("Write-ins", 1),
        ("Over Votes", 0), ("Under Votes", 69),
    ])
    + _c("01 BOARDMAN", "U.S. House", "2", "R", [
        ("Cliff Bentz", 246), ("Andrea Carr", 20), ("Peter J Larson", 53),
        ("Write-ins", 1), ("Over Votes", 0), ("Under Votes", 33),
    ])
    + _c("01 BOARDMAN", "Governor", "", "R", [
        ("Danielle Bethell", 8), ("Hope A Dalrymple", 0), ("Ed Diehl", 119),
        ("Christine Drazan", 167), ("Chris Dudley", 21), ("Kyle M Duyck", 3),
        ("David Medina", 21), ("Robert Neuman", 0), ("Brad T Peters", 2),
        ("Paul J Romero Jr", 1), ("Wen Waddell", 0), ("Martin Ward", 1),
        ("Tim O Youker", 0), ("DeAngelo Leroy Turner", 0), ("Write-ins", 0),
        ("Over Votes", 1), ("Under Votes", 9),
    ])
    + _c("01 BOARDMAN", "State House", "57", "R", [
        ("Jim E Doherty", 109), ("Greg Smith", 231), ("Write-ins", 0),
        ("Over Votes", 0), ("Under Votes", 13),
    ])
    + _c("01 BOARDMAN", _pcp_office("Republican", "01 BOARDMAN"), "", "R", [
        ("Jeremy Gierke", 184), ("Raymond Akers", 86), ("Ethan Akers", 74),
        ("Tiffany B Akers", 61), ("Donna Anderson", 77), ("James Klipfel", 63),
        ("Debbie Imus", 177), ("Sam Irons", 221),
        ("Heather L Baumgartner", 234), ("Naomi Brown", 75),
        ("Deanna Camp", 183), ("David Carney", 73), ("Kelly L Doherty", 92),
        ("Lisa Pratt", 213), ("Rick Stokoe", 228),
        ("Jaimesen S Ratzlaff", 131), ("David Lee Richards", 188),
        ("Cheryl Tallman", 147), ("Sarah Taylor", 168), ("Write-ins", 13),
        ("Over Votes", 10), ("Under Votes", 832),
    ])
    + _c("01 BOARDMAN", "Labor Commissioner", "", "", [
        ("Chris Lynch", 239), ("Christina E Stephenson", 263), ("Write-ins", 4),
        ("Over Votes", 0), ("Under Votes", 176),
    ])
    + _c("01 BOARDMAN", "Judge of the Supreme Court", "Position 4", "", [
        ("Christopher L Garrett", 405), ("Write-ins", 13), ("Over Votes", 0),
        ("Under Votes", 264),
    ])
    + _c("01 BOARDMAN", "Judge of the Court of Appeals", "Position 1", "", [
        ("Ryan T O'Connor", 409), ("Write-ins", 9), ("Over Votes", 0),
        ("Under Votes", 264),
    ])
    + _c("01 BOARDMAN", "Judge of the Court of Appeals", "Position 9", "", [
        ("Jacqueline Kamins", 403), ("Write-ins", 11), ("Over Votes", 0),
        ("Under Votes", 268),
    ])
    + _c("01 BOARDMAN", "Judge of the Court of Appeals", "Position 12", "", [
        ("Erin C Lagesen", 405), ("Write-ins", 11), ("Over Votes", 0),
        ("Under Votes", 266),
    ])
    + _c("01 BOARDMAN", "Judge of the Court of Appeals", "Position 13", "", [
        ("Doug Tookey", 406), ("Write-ins", 8), ("Over Votes", 0),
        ("Under Votes", 268),
    ])
    + _c("01 BOARDMAN", "District Attorney", "", "", [
        ("Justin W Nelson", 427), ("Write-ins", 6), ("Over Votes", 0),
        ("Under Votes", 249),
    ])
    + _c("01 BOARDMAN", "Morrow County Assessor", "", "", [
        ("Michael Gorman", 435), ("Write-ins", 3), ("Over Votes", 0),
        ("Under Votes", 244),
    ])
    + _c("01 BOARDMAN", "Morrow County Commissioner", "Position 2", "", [
        ("Valerie Ballard", 103), ("Kelly Doherty", 136),
        ("Torrie Philippi-Griggs", 411), ("Write-ins", 0), ("Over Votes", 0),
        ("Under Votes", 32),
    ])
    + _c("01 BOARDMAN", "Morrow County Commissioner", "Position 3", "", [
        ("Heather L Baumgartner", 360), ("David Sykes", 229), ("Write-ins", 8),
        ("Over Votes", 0), ("Under Votes", 85),
    ])
    + _c("01 BOARDMAN", "Morrow County Justice of Peace", "", "", [
        ("Glen G Diehl", 441), ("Write-ins", 7), ("Over Votes", 0),
        ("Under Votes", 234),
    ])
    + _c("01 BOARDMAN", "Measure 120", "", "", [
        ("Yes", 21), ("No", 648), ("Over Votes", 0), ("Under Votes", 13),
    ])
)

# -----------------------------------------------------------------------------
# 02 IRRIGON
# -----------------------------------------------------------------------------
IRRIGON = (
    _c("02 IRRIGON", "U.S. Senate", "", "D", [
        ("Jeff Merkley", 71), ("Paul Damian Wells", 25), ("Write-ins", 4),
        ("Over Votes", 0), ("Under Votes", 9),
    ])
    + _c("02 IRRIGON", "U.S. House", "2", "D", [
        ("Chris Beck", 22), ("Mary Doyle", 27), ("Rebecca Mueller", 4),
        ("Patty Snow", 12), ("Dawn Rasmussen", 13), ("Peter Quince", 10),
        ("Write-ins", 4), ("Over Votes", 0), ("Under Votes", 17),
    ])
    + _c("02 IRRIGON", "Governor", "", "D", [
        ("Forest (Fora) Alexander", 1), ("James Atkinson IV", 5),
        ("Cal Kishawi", 1), ("Tina Kotek", 43), ("Donnie M Beckwith", 3),
        ("David W Beem", 6), ("Steve William Laible", 3), ("Brittany Jones", 6),
        ("Tristan Sheppard", 1), ("Miranda Weigler", 5), ("Write-ins", 17),
        ("Over Votes", 0), ("Under Votes", 18),
    ])
    + _c("02 IRRIGON", "State House", "57", "D", [
        ("No Candidate Filed", 0), ("Write-ins", 16), ("Over Votes", 0),
        ("Under Votes", 93),
    ])
    + _c("02 IRRIGON", _pcp_office("Democrat", "02 IRRIGON"), "", "D", [
        ("Write-ins", 27), ("Over Votes", 0), ("Under Votes", 1172),
    ])
    + _c("02 IRRIGON", "U.S. Senate", "", "R", [
        ("Brent Barker", 111), ("Deborah C Brown", 16), ("David A Burch", 14),
        ("Russell McAlmond", 42), ("Jo Rae Perkins", 135),
        ("Timothy Skelton", 9), ("David Brock Smith", 99), ("Write-ins", 3),
        ("Over Votes", 1), ("Under Votes", 60),
    ])
    + _c("02 IRRIGON", "U.S. House", "2", "R", [
        ("Cliff Bentz", 339), ("Andrea Carr", 39), ("Peter J Larson", 65),
        ("Write-ins", 2), ("Over Votes", 1), ("Under Votes", 44),
    ])
    + _c("02 IRRIGON", "Governor", "", "R", [
        ("Danielle Bethell", 7), ("Hope A Dalrymple", 1), ("Ed Diehl", 149),
        ("Christine Drazan", 245), ("Chris Dudley", 34), ("Kyle M Duyck", 3),
        ("David Medina", 32), ("Robert Neuman", 0), ("Brad T Peters", 4),
        ("Paul J Romero Jr", 3), ("Wen Waddell", 0), ("Martin Ward", 0),
        ("Tim O Youker", 0), ("DeAngelo Leroy Turner", 0), ("Write-ins", 1),
        ("Over Votes", 1), ("Under Votes", 10),
    ])
    + _c("02 IRRIGON", "State House", "57", "R", [
        ("Jim E Doherty", 183), ("Greg Smith", 287), ("Write-ins", 0),
        ("Over Votes", 0), ("Under Votes", 20),
    ])
    + _c("02 IRRIGON", _pcp_office("Republican", "02 IRRIGON"), "", "R", [
        ("Dan Gordanier", 345), ("Warren A Kemper", 153),
        ("Michael Connell", 235), ("Kate Close", 280), ("Gary David", 185),
        ("Stuart Dick", 170), ("Jerrold A Moore Jr", 192),
        ("Michael J McNamee", 298), ("Runnisha R McNamee", 261),
        ("Kathy Mendoza", 145), ("Beth Purves", 258), ("Evan Purves", 245),
        ("Dawn Samson", 212), ("Tony M Simpson", 142), ("David Radie", 257),
        ("Debbie Radie", 248), ("Write-ins", 22), ("Over Votes", 55),
        ("Under Votes", 1687),
    ])
    + _c("02 IRRIGON", "Labor Commissioner", "", "", [
        ("Chris Lynch", 351), ("Christina E Stephenson", 314), ("Write-ins", 15),
        ("Over Votes", 0), ("Under Votes", 191),
    ])
    + _c("02 IRRIGON", "Judge of the Supreme Court", "Position 4", "", [
        ("Christopher L Garrett", 534), ("Write-ins", 31), ("Over Votes", 0),
        ("Under Votes", 306),
    ])
    + _c("02 IRRIGON", "Judge of the Court of Appeals", "Position 1", "", [
        ("Ryan T O'Connor", 539), ("Write-ins", 23), ("Over Votes", 0),
        ("Under Votes", 309),
    ])
    + _c("02 IRRIGON", "Judge of the Court of Appeals", "Position 9", "", [
        ("Jacqueline Kamins", 524), ("Write-ins", 27), ("Over Votes", 0),
        ("Under Votes", 320),
    ])
    + _c("02 IRRIGON", "Judge of the Court of Appeals", "Position 12", "", [
        ("Erin C Lagesen", 529), ("Write-ins", 26), ("Over Votes", 2),
        ("Under Votes", 314),
    ])
    + _c("02 IRRIGON", "Judge of the Court of Appeals", "Position 13", "", [
        ("Doug Tookey", 536), ("Write-ins", 24), ("Over Votes", 0),
        ("Under Votes", 311),
    ])
    + _c("02 IRRIGON", "District Attorney", "", "", [
        ("Justin W Nelson", 572), ("Write-ins", 16), ("Over Votes", 0),
        ("Under Votes", 283),
    ])
    + _c("02 IRRIGON", "Morrow County Assessor", "", "", [
        ("Michael Gorman", 562), ("Write-ins", 4), ("Over Votes", 0),
        ("Under Votes", 305),
    ])
    + _c("02 IRRIGON", "Morrow County Commissioner", "Position 2", "", [
        ("Valerie Ballard", 157), ("Kelly Doherty", 293),
        ("Torrie Philippi-Griggs", 327), ("Write-ins", 3), ("Over Votes", 0),
        ("Under Votes", 91),
    ])
    + _c("02 IRRIGON", "Morrow County Commissioner", "Position 3", "", [
        ("Heather L Baumgartner", 385), ("David Sykes", 316), ("Write-ins", 4),
        ("Over Votes", 0), ("Under Votes", 166),
    ])
    + _c("02 IRRIGON", "Morrow County Justice of Peace", "", "", [
        ("Glen G Diehl", 592), ("Write-ins", 3), ("Over Votes", 0),
        ("Under Votes", 276),
    ])
    + _c("02 IRRIGON", "Measure 120", "", "", [
        ("Yes", 23), ("No", 833), ("Over Votes", 0), ("Under Votes", 15),
    ])
)

# -----------------------------------------------------------------------------
# 03 LEXINGTON
# -----------------------------------------------------------------------------
LEXINGTON = (
    _c("03 LEXINGTON", "U.S. Senate", "", "D", [
        ("Jeff Merkley", 18), ("Paul Damian Wells", 7), ("Write-ins", 2),
        ("Over Votes", 0), ("Under Votes", 2),
    ])
    + _c("03 LEXINGTON", "U.S. House", "2", "D", [
        ("Chris Beck", 5), ("Mary Doyle", 5), ("Rebecca Mueller", 2),
        ("Patty Snow", 5), ("Dawn Rasmussen", 4), ("Peter Quince", 0),
        ("Write-ins", 2), ("Over Votes", 0), ("Under Votes", 6),
    ])
    + _c("03 LEXINGTON", "Governor", "", "D", [
        ("Forest (Fora) Alexander", 0), ("James Atkinson IV", 1),
        ("Cal Kishawi", 2), ("Tina Kotek", 14), ("Donnie M Beckwith", 0),
        ("David W Beem", 1), ("Steve William Laible", 0), ("Brittany Jones", 2),
        ("Tristan Sheppard", 1), ("Miranda Weigler", 2), ("Write-ins", 3),
        ("Over Votes", 0), ("Under Votes", 3),
    ])
    + _c("03 LEXINGTON", "State House", "57", "D", [
        ("No Candidate Filed", 0), ("Write-ins", 2), ("Over Votes", 0),
        ("Under Votes", 27),
    ])
    + _c("03 LEXINGTON", _pcp_office("Democrat", "03 LEXINGTON"), "", "D", [
        ("Write-ins", 3), ("Over Votes", 0), ("Under Votes", 55),
    ])
    + _c("03 LEXINGTON", "U.S. Senate", "", "R", [
        ("Brent Barker", 15), ("Deborah C Brown", 8), ("David A Burch", 3),
        ("Russell McAlmond", 17), ("Jo Rae Perkins", 50),
        ("Timothy Skelton", 2), ("David Brock Smith", 54), ("Write-ins", 1),
        ("Over Votes", 0), ("Under Votes", 45),
    ])
    + _c("03 LEXINGTON", "U.S. House", "2", "R", [
        ("Cliff Bentz", 152), ("Andrea Carr", 6), ("Peter J Larson", 17),
        ("Write-ins", 1), ("Over Votes", 1), ("Under Votes", 18),
    ])
    + _c("03 LEXINGTON", "Governor", "", "R", [
        ("Danielle Bethell", 4), ("Hope A Dalrymple", 1), ("Ed Diehl", 69),
        ("Christine Drazan", 83), ("Chris Dudley", 24), ("Kyle M Duyck", 1),
        ("David Medina", 6), ("Robert Neuman", 0), ("Brad T Peters", 0),
        ("Paul J Romero Jr", 0), ("Wen Waddell", 0), ("Martin Ward", 2),
        ("Tim O Youker", 0), ("DeAngelo Leroy Turner", 0), ("Write-ins", 0),
        ("Over Votes", 0), ("Under Votes", 5),
    ])
    + _c("03 LEXINGTON", "State House", "57", "R", [
        ("Jim E Doherty", 67), ("Greg Smith", 108), ("Write-ins", 2),
        ("Over Votes", 0), ("Under Votes", 18),
    ])
    + _c("03 LEXINGTON", _pcp_office("Republican", "03 LEXINGTON"), "", "R", [
        ("Sam Bellamy", 113), ("Corey Miller", 160), ("Write-ins", 0),
        ("Over Votes", 0), ("Under Votes", 117),
    ])
    + _c("03 LEXINGTON", "Labor Commissioner", "", "", [
        ("Chris Lynch", 106), ("Christina E Stephenson", 73), ("Write-ins", 0),
        ("Over Votes", 0), ("Under Votes", 89),
    ])
    + _c("03 LEXINGTON", "Judge of the Supreme Court", "Position 4", "", [
        ("Christopher L Garrett", 135), ("Write-ins", 6), ("Over Votes", 0),
        ("Under Votes", 127),
    ])
    + _c("03 LEXINGTON", "Judge of the Court of Appeals", "Position 1", "", [
        ("Ryan T O'Connor", 132), ("Write-ins", 8), ("Over Votes", 0),
        ("Under Votes", 128),
    ])
    + _c("03 LEXINGTON", "Judge of the Court of Appeals", "Position 9", "", [
        ("Jacqueline Kamins", 134), ("Write-ins", 7), ("Over Votes", 0),
        ("Under Votes", 127),
    ])
    + _c("03 LEXINGTON", "Judge of the Court of Appeals", "Position 12", "", [
        ("Erin C Lagesen", 135), ("Write-ins", 6), ("Over Votes", 0),
        ("Under Votes", 127),
    ])
    + _c("03 LEXINGTON", "Judge of the Court of Appeals", "Position 13", "", [
        ("Doug Tookey", 132), ("Write-ins", 8), ("Over Votes", 0),
        ("Under Votes", 128),
    ])
    + _c("03 LEXINGTON", "District Attorney", "", "", [
        ("Justin W Nelson", 207), ("Write-ins", 2), ("Over Votes", 0),
        ("Under Votes", 59),
    ])
    + _c("03 LEXINGTON", "Morrow County Assessor", "", "", [
        ("Michael Gorman", 201), ("Write-ins", 2), ("Over Votes", 0),
        ("Under Votes", 65),
    ])
    + _c("03 LEXINGTON", "Morrow County Commissioner", "Position 2", "", [
        ("Valerie Ballard", 54), ("Kelly Doherty", 62),
        ("Torrie Philippi-Griggs", 124), ("Write-ins", 0), ("Over Votes", 1),
        ("Under Votes", 27),
    ])
    + _c("03 LEXINGTON", "Morrow County Commissioner", "Position 3", "", [
        ("Heather L Baumgartner", 78), ("David Sykes", 159), ("Write-ins", 2),
        ("Over Votes", 0), ("Under Votes", 29),
    ])
    + _c("03 LEXINGTON", "Morrow County Justice of Peace", "", "", [
        ("Glen G Diehl", 181), ("Write-ins", 4), ("Over Votes", 0),
        ("Under Votes", 83),
    ])
    + _c("03 LEXINGTON", "Measure 120", "", "", [
        ("Yes", 6), ("No", 253), ("Over Votes", 0), ("Under Votes", 9),
    ])
)

# -----------------------------------------------------------------------------
# 04 IONE
# -----------------------------------------------------------------------------
IONE = (
    _c("04 IONE", "U.S. Senate", "", "D", [
        ("Jeff Merkley", 29), ("Paul Damian Wells", 3), ("Write-ins", 0),
        ("Over Votes", 0), ("Under Votes", 4),
    ])
    + _c("04 IONE", "U.S. House", "2", "D", [
        ("Chris Beck", 9), ("Mary Doyle", 7), ("Rebecca Mueller", 2),
        ("Patty Snow", 0), ("Dawn Rasmussen", 4), ("Peter Quince", 3),
        ("Write-ins", 1), ("Over Votes", 0), ("Under Votes", 10),
    ])
    + _c("04 IONE", "Governor", "", "D", [
        ("Forest (Fora) Alexander", 0), ("James Atkinson IV", 2),
        ("Cal Kishawi", 0), ("Tina Kotek", 14), ("Donnie M Beckwith", 2),
        ("David W Beem", 1), ("Steve William Laible", 1), ("Brittany Jones", 2),
        ("Tristan Sheppard", 1), ("Miranda Weigler", 0), ("Write-ins", 5),
        ("Over Votes", 0), ("Under Votes", 8),
    ])
    + _c("04 IONE", "State House", "57", "D", [
        ("No Candidate Filed", 0), ("Write-ins", 3), ("Over Votes", 0),
        ("Under Votes", 33),
    ])
    + _c("04 IONE", _pcp_office("Democrat", "04 IONE"), "", "D", [
        ("Write-ins", 2), ("Over Votes", 0), ("Under Votes", 70),
    ])
    + _c("04 IONE", "U.S. Senate", "", "R", [
        ("Brent Barker", 30), ("Deborah C Brown", 8), ("David A Burch", 3),
        ("Russell McAlmond", 17), ("Jo Rae Perkins", 37),
        ("Timothy Skelton", 3), ("David Brock Smith", 45), ("Write-ins", 0),
        ("Over Votes", 0), ("Under Votes", 36),
    ])
    + _c("04 IONE", "U.S. House", "2", "R", [
        ("Cliff Bentz", 137), ("Andrea Carr", 4), ("Peter J Larson", 22),
        ("Write-ins", 0), ("Over Votes", 0), ("Under Votes", 16),
    ])
    + _c("04 IONE", "Governor", "", "R", [
        ("Danielle Bethell", 1), ("Hope A Dalrymple", 0), ("Ed Diehl", 57),
        ("Christine Drazan", 88), ("Chris Dudley", 24), ("Kyle M Duyck", 0),
        ("David Medina", 5), ("Robert Neuman", 0), ("Brad T Peters", 0),
        ("Paul J Romero Jr", 1), ("Wen Waddell", 0), ("Martin Ward", 0),
        ("Tim O Youker", 0), ("DeAngelo Leroy Turner", 0), ("Write-ins", 0),
        ("Over Votes", 0), ("Under Votes", 3),
    ])
    + _c("04 IONE", "State House", "57", "R", [
        ("Jim E Doherty", 59), ("Greg Smith", 108), ("Write-ins", 4),
        ("Over Votes", 0), ("Under Votes", 8),
    ])
    + _c("04 IONE", _pcp_office("Republican", "04 IONE"), "", "R", [
        ("Clinton R Carlson", 142), ("Sarah Carlson", 126), ("Write-ins", 8),
        ("Over Votes", 0), ("Under Votes", 82),
    ])
    + _c("04 IONE", "Labor Commissioner", "", "", [
        ("Chris Lynch", 123), ("Christina E Stephenson", 61), ("Write-ins", 0),
        ("Over Votes", 1), ("Under Votes", 80),
    ])
    + _c("04 IONE", "Judge of the Supreme Court", "Position 4", "", [
        ("Christopher L Garrett", 147), ("Write-ins", 5), ("Over Votes", 0),
        ("Under Votes", 113),
    ])
    + _c("04 IONE", "Judge of the Court of Appeals", "Position 1", "", [
        ("Ryan T O'Connor", 149), ("Write-ins", 2), ("Over Votes", 0),
        ("Under Votes", 114),
    ])
    + _c("04 IONE", "Judge of the Court of Appeals", "Position 9", "", [
        ("Jacqueline Kamins", 141), ("Write-ins", 2), ("Over Votes", 0),
        ("Under Votes", 122),
    ])
    + _c("04 IONE", "Judge of the Court of Appeals", "Position 12", "", [
        ("Erin C Lagesen", 138), ("Write-ins", 3), ("Over Votes", 0),
        ("Under Votes", 124),
    ])
    + _c("04 IONE", "Judge of the Court of Appeals", "Position 13", "", [
        ("Doug Tookey", 139), ("Write-ins", 2), ("Over Votes", 0),
        ("Under Votes", 124),
    ])
    + _c("04 IONE", "District Attorney", "", "", [
        ("Justin W Nelson", 190), ("Write-ins", 5), ("Over Votes", 0),
        ("Under Votes", 70),
    ])
    + _c("04 IONE", "Morrow County Assessor", "", "", [
        ("Michael Gorman", 185), ("Write-ins", 2), ("Over Votes", 0),
        ("Under Votes", 78),
    ])
    + _c("04 IONE", "Morrow County Commissioner", "Position 2", "", [
        ("Valerie Ballard", 63), ("Kelly Doherty", 62),
        ("Torrie Philippi-Griggs", 123), ("Write-ins", 0), ("Over Votes", 1),
        ("Under Votes", 16),
    ])
    + _c("04 IONE", "Morrow County Commissioner", "Position 3", "", [
        ("Heather L Baumgartner", 80), ("David Sykes", 156), ("Write-ins", 6),
        ("Over Votes", 0), ("Under Votes", 23),
    ])
    + _c("04 IONE", "Morrow County Justice of Peace", "", "", [
        ("Glen G Diehl", 174), ("Write-ins", 2), ("Over Votes", 0),
        ("Under Votes", 89),
    ])
    + _c("04 IONE", "Measure 120", "", "", [
        ("Yes", 9), ("No", 248), ("Over Votes", 0), ("Under Votes", 8),
    ])
)

# -----------------------------------------------------------------------------
# 05 HEPPNER
# -----------------------------------------------------------------------------
HEPPNER = (
    _c("05 HEPPNER", "U.S. Senate", "", "D", [
        ("Jeff Merkley", 96), ("Paul Damian Wells", 18), ("Write-ins", 1),
        ("Over Votes", 0), ("Under Votes", 16),
    ])
    + _c("05 HEPPNER", "U.S. House", "2", "D", [
        ("Chris Beck", 21), ("Mary Doyle", 25), ("Rebecca Mueller", 21),
        ("Patty Snow", 7), ("Dawn Rasmussen", 17), ("Peter Quince", 3),
        ("Write-ins", 2), ("Over Votes", 0), ("Under Votes", 35),
    ])
    + _c("05 HEPPNER", "Governor", "", "D", [
        ("Forest (Fora) Alexander", 5), ("James Atkinson IV", 2),
        ("Cal Kishawi", 1), ("Tina Kotek", 61), ("Donnie M Beckwith", 1),
        ("David W Beem", 3), ("Steve William Laible", 2), ("Brittany Jones", 7),
        ("Tristan Sheppard", 5), ("Miranda Weigler", 3), ("Write-ins", 13),
        ("Over Votes", 0), ("Under Votes", 28),
    ])
    + _c("05 HEPPNER", "State House", "57", "D", [
        ("No Candidate Filed", 0), ("Write-ins", 24), ("Over Votes", 0),
        ("Under Votes", 107),
    ])
    + _c("05 HEPPNER", _pcp_office("Democrat", "05 HEPPNER"), "", "D", [
        ("Write-ins", 12), ("Over Votes", 0), ("Under Votes", 643),
    ])
    + _c("05 HEPPNER", "U.S. Senate", "", "R", [
        ("Brent Barker", 79), ("Deborah C Brown", 11), ("David A Burch", 8),
        ("Russell McAlmond", 47), ("Jo Rae Perkins", 96),
        ("Timothy Skelton", 6), ("David Brock Smith", 131), ("Write-ins", 0),
        ("Over Votes", 0), ("Under Votes", 92),
    ])
    + _c("05 HEPPNER", "U.S. House", "2", "R", [
        ("Cliff Bentz", 350), ("Andrea Carr", 22), ("Peter J Larson", 53),
        ("Write-ins", 0), ("Over Votes", 0), ("Under Votes", 45),
    ])
    + _c("05 HEPPNER", "Governor", "", "R", [
        ("Danielle Bethell", 8), ("Hope A Dalrymple", 1), ("Ed Diehl", 137),
        ("Christine Drazan", 222), ("Chris Dudley", 65), ("Kyle M Duyck", 2),
        ("David Medina", 18), ("Robert Neuman", 4), ("Brad T Peters", 1),
        ("Paul J Romero Jr", 5), ("Wen Waddell", 0), ("Martin Ward", 0),
        ("Tim O Youker", 0), ("DeAngelo Leroy Turner", 0), ("Write-ins", 0),
        ("Over Votes", 0), ("Under Votes", 7),
    ])
    + _c("05 HEPPNER", "State House", "57", "R", [
        ("Jim E Doherty", 167), ("Greg Smith", 273), ("Write-ins", 4),
        ("Over Votes", 0), ("Under Votes", 26),
    ])
    + _c("05 HEPPNER", _pcp_office("Republican", "05 HEPPNER"), "", "R", [
        ("Ken W Bailey", 362), ("Dale D Bates", 356), ("Jack Meligan", 331),
        ("Karen Temple", 344), ("Dick Temple", 345), ("Write-ins", 8),
        ("Over Votes", 0), ("Under Votes", 604),
    ])
    + _c("05 HEPPNER", "Labor Commissioner", "", "", [
        ("Chris Lynch", 303), ("Christina E Stephenson", 230), ("Write-ins", 6),
        ("Over Votes", 1), ("Under Votes", 227),
    ])
    + _c("05 HEPPNER", "Judge of the Supreme Court", "Position 4", "", [
        ("Christopher L Garrett", 454), ("Write-ins", 11), ("Over Votes", 1),
        ("Under Votes", 301),
    ])
    + _c("05 HEPPNER", "Judge of the Court of Appeals", "Position 1", "", [
        ("Ryan T O'Connor", 457), ("Write-ins", 11), ("Over Votes", 1),
        ("Under Votes", 298),
    ])
    + _c("05 HEPPNER", "Judge of the Court of Appeals", "Position 9", "", [
        ("Jacqueline Kamins", 448), ("Write-ins", 11), ("Over Votes", 1),
        ("Under Votes", 307),
    ])
    + _c("05 HEPPNER", "Judge of the Court of Appeals", "Position 12", "", [
        ("Erin C Lagesen", 450), ("Write-ins", 11), ("Over Votes", 1),
        ("Under Votes", 305),
    ])
    + _c("05 HEPPNER", "Judge of the Court of Appeals", "Position 13", "", [
        ("Doug Tookey", 447), ("Write-ins", 13), ("Over Votes", 1),
        ("Under Votes", 306),
    ])
    + _c("05 HEPPNER", "District Attorney", "", "", [
        ("Justin W Nelson", 575), ("Write-ins", 11), ("Over Votes", 1),
        ("Under Votes", 180),
    ])
    + _c("05 HEPPNER", "Morrow County Assessor", "", "", [
        ("Michael Gorman", 593), ("Write-ins", 15), ("Over Votes", 1),
        ("Under Votes", 158),
    ])
    + _c("05 HEPPNER", "Morrow County Commissioner", "Position 2", "", [
        ("Valerie Ballard", 162), ("Kelly Doherty", 208),
        ("Torrie Philippi-Griggs", 325), ("Write-ins", 5), ("Over Votes", 1),
        ("Under Votes", 66),
    ])
    + _c("05 HEPPNER", "Morrow County Commissioner", "Position 3", "", [
        ("Heather L Baumgartner", 234), ("David Sykes", 462), ("Write-ins", 7),
        ("Over Votes", 1), ("Under Votes", 63),
    ])
    + _c("05 HEPPNER", "Morrow County Justice of Peace", "", "", [
        ("Glen G Diehl", 575), ("Write-ins", 14), ("Over Votes", 1),
        ("Under Votes", 177),
    ])
    + _c("05 HEPPNER", "Measure 120", "", "", [
        ("Yes", 24), ("No", 721), ("Over Votes", 0), ("Under Votes", 22),
    ])
)

ALL_ROWS = BOARDMAN + IRRIGON + LEXINGTON + IONE + HEPPNER

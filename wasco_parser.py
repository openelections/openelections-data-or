import xlrd
import unicodecsv

headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

OFFICES = ['U.S. SENATOR', 'REP IN CONGRESS 2ND DIST', 'GOVERNOR - DEM', 'STATE REPRESENTATIVE DIST 59 - DEM',
'STATE REPRESENTATIVE DIST 57 - DEM', 'U S PRESIDENT', 'SECRETARY OF STATE', 'STATE TREASURER', 'ATTORNEY GENERAL',
'STATE SENATOR - DIST 30', 'STATE SENATOR - DIST 29', 'STATE REPRESENTATIVE - DIST 59', 'STATE REPRESENTATIVE - DIST 57',
'GOVERNOR', 'STATE REPRESENTATIVE DIST 59 ', 'U S PRESIDENT / VICE PRESIDENT', 'U S REP IN CONGRESS 2ND DIST',
'STATE SEN 27TH DIST', 'STATE SEN 28TH DIST ', 'STATE REP 55TH DIST', 'STATE REP 56TH DIST', 'STATE REP 59TH DIST',
'REP IN CONG 2ND DIST', 'U S SENATOR']

OFFICE_LOOKUP = {
    'U.S. SENATOR' : 'U.S. Senate', 'REP IN CONGRESS 2ND DIST' : 'U.S. House', 'GOVERNOR - DEM' : 'Governor', 'STATE REPRESENTATIVE DIST 59 - DEM' : 'State House',
    'STATE REPRESENTATIVE DIST 57 - DEM' : 'State House', 'SECRETARY OF STATE' : 'Secretary of State', 'ATTORNEY GENERAL' : 'Attorney General', 'REP IN CONG 2ND DIST': 'U.S. House',
    'STATE TREASURER' : 'State Treasurer', 'STATE SENATOR - DIST 30' : 'State Senate', 'U S PRESIDENT' : 'President', 'STATE SENATOR - DIST 29' : 'State Senate',
    'U S PRESIDENT / VICE PRESIDENT' : 'President', 'STATE REPRESENTATIVE - DIST 59': 'State House', 'STATE REPRESENTATIVE - DIST 57': 'State House', 'U S SENATOR': 'U.S. Senate',
    'U S REP IN CONGRESS 2ND DIST': 'U.S. House', 'STATE SEN 27TH DIST': 'State Senate', 'STATE SEN 28TH DIST ': 'State Senate', 'STATE REP 55TH DIST': 'State House',
    'STATE REP 56TH DIST' : 'State House', 'STATE REP 59TH DIST': 'State House', 'GOVERNOR' : 'Governor', 'STATE REPRESENTATIVE DIST 59 ': 'State House'
}

def process_file(path):
    xlsfile = xlrd.open_workbook(path)
    for sheet in xlsfile.sheets():
        process_sheet(sheet)

def process_sheet(sheet):
    if "General" in sheet.name:
        process_general(sheet)
    else:
        process_primary(sheet)

def process_general(sheet):
    results = []
    office = None
    year = "20"+"".join([s for s in sheet.name if s.isdigit()])
    filename = "%s__or__general__wasco__precinct.csv" % year
    with open(filename, 'wb') as csvfile:
        w = unicodecsv.writer(csvfile, encoding='utf-8')
        w.writerow(headers)
        cand_col, party_col, precinct_cols, precincts = get_general_cols(sheet)
        for i in xrange(1, sheet.nrows):
            if sheet.row_values(i)[0] == '' and sheet.row_values(i)[1] == '':
                continue
            if sheet.row_values(i)[0] in OFFICES:
                office, district = get_office_and_district(sheet.row_values(i)[0])
                continue
            if office:
                votes = []
                candidate = sheet.row_values(i)[cand_col].strip()
                party = sheet.row_values(i)[party_col].strip()
                for p in range(precinct_cols[0], precinct_cols[1]):
                    votes.append(sheet.row_values(i)[p])
                with_precincts = zip(precincts, votes)
                for precinct, total in with_precincts:
                    w.writerow(['Wasco', precinct, office, district, party, candidate, total])


def get_office_and_district(cell):
    office = OFFICE_LOOKUP[cell]
    if 'DIST' in cell:
        district = "".join([s for s in cell if s.isdigit()])
    else:
        district = None
    return [office, district]

def get_general_cols(sheet):
    if '12' in sheet.name or '14' in sheet.name:
        cand_col = 0
        party_col = 1
        precinct_cols = (2, 16)
        precincts = ['PREC '+str(x) for x in xrange(1,15)]
    elif '10' in sheet.name:
        cand_col = 0
        party_col = 1
        precinct_cols = (2, 12)
        precincts = ['PREC '+str(x) for x in xrange(1,11)]
    elif '00' in sheet.name:
        cand_col = 0
        party_col = 1
        precinct_cols = (2, sheet.ncols-1)
        precincts = ['PREC 19', 'PREC 20', 'PREC 21', 'PREC 22', 'PREC 23', 'PREC 24', 'PREC 25',
        'PREC 26', 'PREC 27', 'PREC 28', 'PREC 29', 'PREC 30', 'PREC 31', 'PREC 80', 'PREC 81',
        'PREC 82', 'PREC 83', 'PREC 84', 'PREC 85', 'PREC 86', 'PREC 87', 'PREC 88', 'PREC 89']
    else:
        cand_col = 1
        party_col = 2
        precinct_cols = (3, 13)
        precincts = ['PREC '+str(x) for x in xrange(1,11)]
    return [cand_col, party_col, precinct_cols, precincts]

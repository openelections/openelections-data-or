# JSON parser for Secretary of State site with precinct results
import requests
import unicodecsv

RACE_TYPES = ['FED', 'SWPAR', 'SENATE', 'HOUSE']
COUNTIES = { 'Josephine': 1,'Curry': 2,'Jackson': 3,'Coos': 4,'Klamath': 5,'Lake': 6,'Douglas': 7,'Harney': 8,'Lane': 9,'Deschutes': 10,'Malheur': 11,'Crook': 12,'Benton': 13,'Linn': 14,'Jefferson': 15,'Grant': 16,'Lincoln': 17,'Wheeler': 18,'Polk': 19,'Baker': 20,'Marion': 21,'Yamhill': 22,'Clackamas': 23,'Wasco': 24,'Hood River': 25,'Multnomah': 26,'Sherman': 27,'Washington': 28,'Tillamook': 29,'Gilliam': 30,'Union': 31,'Morrow': 32,'Wallowa': 33,'Umatilla': 34,'Columbia': 35,'Clatsop': 36 }
STATEWIDE_RACES = {'300019191': 'President', '300019192': 'U.S. Senate', '300019194': 'U.S. House, 1', '300019195': 'U.S. House, 2', '300019196': 'U.S. House, 3', '300019197': 'U.S. House, 4', '300019198': 'U.S. House, 5', '300019199': 'Attorney General', '300019200': 'Governor', '300019201': 'Secretary of State', '300019202': 'State Treasurer'}

def fetch_state_senate_races():
    f = open('state_senate_2016.csv', 'r')
    reader = unicodecsv.DictReader(f, encoding='utf-8')
    return [row for row in reader]

def fetch_state_house_races():
    f = open('state_house_2016.csv', 'r')
    reader = unicodecsv.DictReader(f, encoding='utf-8')
    return [row for row in reader]

def county_results(county):
    state_senate = fetch_state_senate_races()
    state_house = fetch_state_house_races()
    values = []
    county_id = COUNTIES[county]
    for race_type in RACE_TYPES:
        url = "http://orresultswebservices.azureedge.net/ResultsAjax.svc/GetMapData?type=%s&category=PREC&osn=0&party=0&county=%s" % (race_type, str(county_id))
        r = requests.get(url)
        json = r.json()
        results = json['d']
        for result in results:
            if race_type == 'SENATE':
                race = [x for x in state_senate if x['ContestID'] == str(result['RaceID'])][0]
                office = 'State Senate'
                district = race['AreaNum'].split()[1]
            elif race_type == 'HOUSE':
                race = [x for x in state_house if x['ContestID'] == str(result['RaceID'])][0]
                office = 'State House'
                district = race['AreaNum'].split()[1]
            else:
                office = STATEWIDE_RACES[str(result['RaceID'])]
                if 'U.S. House' in office:
                    office, district = office.split(', ')
                else:
                    district = None
            values.append([county, result['PrecinctName'], office, district, result['PartyCode'], result['calcCandidate'], result['calcCandidateVotes']])
    return values

def write_csv(county):
    values = county_results(county)
    filename = '20161108__or__general__%s__precinct.csv' % county.lower()
    with open(filename, 'wb') as csvfile:
        w = unicodecsv.writer(csvfile, encoding='utf-8')
        w.writerow(['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes'])
        for row in values:
            w.writerow(row)
    

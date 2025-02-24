import xml.etree.ElementTree as ET
import csv

def xml_to_csv(xml_file, csv_file):
    """
    Converts precinct-level results from an XML file to a CSV file.

    Args:
        xml_file (str): Path to the input XML file.
        csv_file (str): Path to the output CSV file.
    """

    tree = ET.parse(xml_file)
    root = tree.getroot()

    with open(csv_file, 'w', newline='') as csvfile:
        csv_writer = csv.writer(csvfile)

        # Write the header row
        csv_writer.writerow(['county', 'precinct', 'office', 'district', 'party', 'candidate', 'election_day', 'early_voting', 'absentee', 'votes'])

        # Iterate through each Contest
        for contest in root.find('Election').find('ContestList').findall('Contest'):
            county = root.find('JurisdictionMap').find('Jurisdiction').get('title')
            office = contest.get('title')
            district = root.find('DistrictMap').find(f"District[ImportID='{contest.get('districtId')}-']")
            
            if district is not None:
                district_name = district.get('name')
            else:
                 district_name = 'Jurisdiction Wide'

            # Iterate through each Candidate in the Contest
            for candidate in contest.findall('Candidate'):
                candidate_name = candidate.get('name')
                party_id = candidate.get('partyId')

                #Handle Party not being found cases:
                party_element = root.find('PartyMap').find(f"Party[@id='{party_id}']")
                if party_element is not None:
                    party_name = party_element.get('name')
                else:
                    party_name = "Unknown Party"

                # Extract votes for each ContestGroup (Absentee, Early Voting, Election Day)
                election_day_votes = 0
                early_voting_votes = 0
                absentee_votes = 0

                for group in contest.findall('ContestGroup'):
                   group_id = group.get('groupId')
                   
                   if group_id == '1': #Election Day
                       for candidate_group in candidate.findall('CandidateGroup'):
                            if candidate_group.get('groupId') == group_id:
                                election_day_votes = candidate_group.get('totalVotes')
                                break
                            
                   elif group_id == '2': #Early Voting
                        for candidate_group in candidate.findall('CandidateGroup'):
                            if candidate_group.get('groupId') == group_id:
                                early_voting_votes = candidate_group.get('totalVotes')
                                break

                   elif group_id == '3': #Absentee
                        for candidate_group in candidate.findall('CandidateGroup'):
                            if candidate_group.get('groupId') == group_id:
                                absentee_votes = candidate_group.get('totalVotes')
                                break

                    
                # Write the row to the CSV file for each precinct with Absentee votes
                if absentee_votes != '0':
                    for precinct_group in contest.find(f"ContestGroup[@groupId='3']") .findall('ContestGroupVotes'):
                        precinct_id = precinct_group.get('refPrecinctId')
                        precinct_name = root.find('JurisdictionMap').find('Jurisdiction').find(f"Precinct[@id='{precinct_id}']").get('name')

                        if precinct_group is not None:
                            for votes_element in candidate.findall(f"Votes[@refPrecinctId='{precinct_id}']"):
                                if votes_element.get('groupId') == '3':  # Only output Absentee votes with correct Precinct ID
                                    csv_writer.writerow([county, precinct_name, office, district_name, party_name, candidate_name, 0, 0, votes_element.text, votes_element.text])

    print(f"Successfully converted {xml_file} to {csv_file}")

# Example usage:
xml_file = '/Users/dwillis/code/openelections-sources-or/2024/general/2024 Deschutes County, OR precinct-level results.xml'  # Replace with your XML file name
csv_file = 'deschutes_county_election_results.csv'  # Replace with your desired CSV file name
xml_to_csv(xml_file, csv_file)

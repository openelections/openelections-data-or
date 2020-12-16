county <- "hood_river"

precinct_results <- read_csv(here('2020', paste0('20201103__or__general__', 
                                                 county, 
                                                 '__precinct.csv')))

county_results <- precinct_results %>%
  group_by(office, district, party, candidate) %>%
  summarize(votes = sum(votes))


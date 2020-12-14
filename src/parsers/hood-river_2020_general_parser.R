library(tidyverse)
library(here)

hoodriver_folder <- here("..", "hood_river")

hoodriver_files <- list.files(path = hoodriver_folder, 
                             pattern = ".csv",
                             full.names = TRUE)

hoodriver <- lapply(hoodriver_files, read_csv, col_names = c('candidate', 'votes')) %>%
  magrittr::set_names(hoodriver_files) %>%
  bind_rows(.id = "file") %>%
  mutate(file = str_extract(file, "[^/]*$") %>% str_remove("\\.csv$")) %>%
  separate(file, into = c('office', 'precinct'), sep = "_") %>%
  mutate(office = fct_recode(office,
                             "Attorney General" = "attorney-general",
                             "President" = "president",
                             "Secretary of State" = "sec-state",
                             "State Representative" = "state-house",
                             "Treasurer" = "treasurer",
                             "Turnout" = "turnout",
                             "U.S. House" = "us-house",
                             "U.S. Senate" = "us-senate") %>% as.character(),
         office = case_when(
           candidate == "Registered Voters" ~ "Registered Voters",
           candidate == "Ballots Cast" ~ "Ballots Cast",
           TRUE ~ office
         ),
         district = case_when(
           office == "State Representative" ~ 52,
           office == "U.S. House" ~ 2
         ),
         candidate = fct_recode(candidate,
                                "Write-ins" = "Write-in",
                                "Over Votes" = "Overvotes",
                                "Under Votes" = "Undervotes") %>% as.character(),
         party = fct_collapse(candidate,
                              "DEM" = c("Joseph R Biden / Kamala D Harris",
                                        "Ellen Rosenblum",
                                        "Shemia Fagan",
                                        "Anna Williams",
                                        "Tobias Read",
                                        "Alex Spenser",
                                        "Jeff Merkley"),
                              "REP" = c("Donald J Trump / Michael R Pence",
                                        "Michael Cross",
                                        "Kim Thatcher",
                                        "Jeff Helfrich",
                                        "Jeff Gudman",
                                        "Cliff Bentz",
                                        "Jo Rae Perkins"),
                              "LBT" = c("Jo Jorgensen / Jeremy (Spike) Cohen",
                                        "Lars D H Hedbor",
                                        "Kyle Markley",
                                        "Stephen D Alder",
                                        "Robert Werch",
                                        "Gary Dye"),
                              "PGP" = c("Howie Hawkins / Angela Walker",
                                        "Nathalie Paravicini",
                                        "Ibrahim A Taher"),
                              "PRO" = c("Dario Hunter / Dawn Neptune Adams"),
                              "CON" = c("Michael P Marsh"),
                              "IND" = c("Chris Henry"),
                              NULL = c("Write-ins",
                                       "Over Votes",
                                       "Under Votes")) %>% as.character()) %>%
  filter(candidate != "Total") %>%
  transmute(county = "Hood River",
            precinct,
            office,
            district,
            party,
            candidate,
            votes)

write_csv(hoodriver, here('2020', '20201103__or__general__hood_river__precinct.csv'))

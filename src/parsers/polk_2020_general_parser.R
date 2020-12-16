library(tidyverse)
library(here)

polk_folder <- here("..", "polk")

polk_files <- list.files(path = polk_folder, 
                             pattern = ".csv",
                             full.names = TRUE)

polk <- lapply(polk_files, read_csv, col_names = c('candidate', 'votes'), col_types = "cc") %>%
  magrittr::set_names(polk_files) %>%
  bind_rows(.id = "file") %>%
  mutate(file = str_extract(file, "[^/]*$") %>% str_remove("\\.csv$")) %>%
  separate(file, into = c('office', 'precinct', 'district'), sep = "_") %>%
  mutate(office = fct_recode(office,
                             "Attorney General" = "attorney-general",
                             "President" = "president",
                             "Secretary of State" = "sec-state",
                             "State Representative" = "state-house",
                             "State Senator" = "state-senate",
                             "Treasurer" = "treasurer",
                             "Turnout" = "turnout",
                             "U.S. House" = "us-house",
                             "U.S. Senate" = "us-senate") %>% as.character(),
         office = case_when(
           candidate == "Registered Voters - Total" ~ "Registered Voters",
           candidate == "Ballots Cast - Total" ~ "Ballots Cast",
           TRUE ~ office
         ),
         candidate = fct_collapse(candidate,
                                "Write-ins" = "Write-In Totals",
                                "Over Votes" = "Overvotes",
                                "Under Votes" = "Undervotes",
                                "LBT Jo Jorgensen / Jeremy (Spike) Cohen" = "LBT Jo Jorgensen / Jeremy (Spike)",
                                "DEM Joseph R Biden / Kamala D Harris" = "DEM Joseph R Biden / Kamala D",
                                "REP Donald J Trump / Michael R Pence" = "REP Donald J Trump / Michael R",
                                NULL = c("Ballots Cast - Total", "Registered Voters - Total")) %>% as.character(),
         party = str_extract(candidate, "^[A-Z]{3}"),
         candidate = str_remove(candidate, "^[A-Z]{3} "),
         district = case_when(
           !is.na(district) ~ district,
           office == "U.S. House" ~ "5"
         ) %>% as.numeric()) %>%
  filter(!(candidate %in% c("Total Votes Cast", "Contest Totals")),
         !(office %in% "Turnout")) %>%
  transmute(county = "Polk",
            precinct,
            office,
            district,
            party,
            candidate,
            votes = str_remove_all(votes, "[^\\d]") %>% as.numeric())

write_csv(polk, here('2020', '20201103__or__general__polk__precinct.csv'))

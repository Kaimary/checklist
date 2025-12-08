select dogs.owner_id, owners.zip_code from owners join dogs on owners.owner_id = dogs.owner_id group by dogs.owner_id order by sum ( treatments.cost_of_treatment ) desc limit 1
select count ( * ) from cars_data where cylinders > 6
select count ( distinct continent ) from countrylanguage where language = 'Chinese'
select template_type_code from templates group by template_type_code having count ( * ) < 3
select count ( * ) from airlines join flights on airlines.uid = flights.uid where airlines.airline = 'United Airlines' and flights.sourceairport = 'ASY'
select count ( * ) from documents
select airlines.airline from airlines join flights on airlines.airline = flights.airline group by airlines.airline having count ( * ) >= 10
select name from shop where shop_id not in ( select shop_id from hiring )
select template_type_code, template_type_description from ref_template_types
select singer.name from singer_in_concert join concert on singer_in_concert.concert_id = concert.concert_id join singer on singer_in_concert.singer_id = singer.singer_id where concert.year = 2014
select max ( mpg ) from cars_data where cylinders = 8 or year < 1980
select record_company from orchestra where year_of_founded < 2003 intersect select record_company from orchestra where year_of_founded > 2003
select id from tv_channel group by id having count ( * ) > 2
select airportname from airports where airportcode not in ( select sourceairport from flights ) or destairport not in ( select sourceairport from flights )
select name, result from battle where bulgarian_commander!= 'Boril'
select birth_year, citizenship from singer
select money_rank from poker_player order by earnings desc limit 1
select avg ( age ) from dogs
select concert.concert_name, concert.theme, count ( * ) from singer_in_concert join concert on singer_in_concert.concert_id = concert.concert_id group by singer_in_concert.concert_id
select record_company, count ( * ) from orchestra group by record_company
select battle.id, battle.name from battle join death on battle.id = death.caused_by_ship_id group by battle.id having sum ( death.killed ) > 10
select count ( * ) from pets where weight > 10
select name, grade from highschooler
select state from owners intersect select state from professionals
select count ( * ) from flights
select nationality, count ( * ) from people group by nationality
select population, lifeexpectancy from country where name = 'Brazil'
select template_id, count ( * ) from documents group by template_id
select first_name, last_name from players order by birth_date asc
select count ( * ) from singer
select count ( distinct student_enrolment_courses.student_enrolment_id ), transcript_contents.student_course_id from student_enrolment_courses join transcript_contents on student_enrolment_courses.student_enrolment_id = transcript_contents.student_course_id group by student_enrolment_courses.student_enrolment_id order by count ( * ) desc limit 1
select count ( distinct pettype ) from pets
select count ( * ) from paragraphs
select countryname from countries except select countries.countryname from countries join car_makers on countries.countryid = car_makers.country
select pettype, avg ( weight ) from pets group by pettype
select visitor.id, visitor.name, visitor.level_of_membership from visitor join visit on visitor.id = visit.visitor_id order by total_spent desc limit 1
select song.title, singer.name from singer join song on singer.singer_id = song.singer_id
select car_names.make, car_names.model from car_names join cars_data on car_names.makeid = cars_data.id where cars_data.horsepower!= min ( select min ( horsepower ) from cars_data )
select other_details from paragraphs where paragraph_text = 'Korea'
select count ( distinct degree_program_id ) from degree_programs
select air_date from tv_series where episode = 'A Love of a Lifetime'
select players.first_name, players.last_name from players join matches on players.player_id = matches.winner_id where matches.year = 2013 intersect select players.winner_name from players join matches on players.player_id = matches.winner_id where matches.year = 2016
select country from singer where age > 40 intersect select country from singer where age < 30
select flightno from flights where sourceairport = 'APG'
select count ( * ) from owners where owner_id not in ( select owner_id from dogs )
select max ( area_code ), min ( area_code ) from area_code_state
select student_id, count ( * ) from likes group by student_id
select highschooler.name from highschooler join friend on highschooler.id = friend.student_id group by friend.student_id having count ( * ) >= 3
select nationality, count ( * ) from people group by nationality
select flights.flightno from airports join flights on airports.airportcode = flights.sourceairport where airports.city = 'Aberdeen'
select language, count ( * ) from tv_channel group by language
select country, count ( * ) from singer group by country
select count ( * ) from cartoon where written_by = 'Joseph Kuhr'
select count ( * ) from battle where id not in ( select lost_in_battle from ship where tonnage = '225' )
select max ( share ), min ( share ) from performance where type!= 'Live final'
select employee.name from employee join evaluation on employee.employee_id = evaluation.employee_id group by employee.employee_id order by count ( * ) desc limit 1
select language, count ( * ) from tv_channel group by language order by count ( * ) asc limit 1
select count ( * ) from concert where year = 2014 or year = 2015
select name, location, district from shop order by number_products desc
select count ( * ) from cars_data where horsepower > 150
select name, country from singer where song_name like '%Hey%'
select transcript_date from transcripts order by transcript_date desc limit 1
select avg ( earnings ) from poker_player
select template_type_code from templates group by template_type_code order by count ( * ) desc limit 1
select grade from highschooler group by grade order by count ( * ) desc limit 1
select governmentform, sum ( population ) from country group by governmentform having avg ( lifeexpectancy ) >= 72
select transcripts.transcript_date, transcripts.transcript_id from transcripts join transcript_contents on transcripts.transcript_id = transcript_contents.transcript_id group by transcript_contents.transcript_id order by count ( * ) asc limit 1
select name from teacher where age = 32 or age = 33
select car_names.make, cars_data.year from cars_data join car_names on cars_data.id = car_names.makeid order by cars_data.year asc limit 1
select max ( weight ), pettype from pets group by pettype
select count ( distinct professional_id ) from treatments
select population, name, headofstate from country order by surfacearea desc limit 1
select student_enrolment.student_id, students.first_name, students.middle_name, students.last_name, count ( * ), student_enrolment.student_id from student_enrolment join students on student_enrolment.student_id = students.student_id group by student_enrolment.student_id order by count ( * ) desc limit 1
select templates.template_id, templates.template_type_code from templates join documents on templates.template_id = documents.template_id group by templates.template_id order by count ( * ) desc limit 1
select nationality from people group by nationality order by count ( * ) desc limit 1
select people.name from poker_player join people on poker_player.people_id = people.people_id order by poker_player.earnings desc
select cars_data.accelerate from car_names join cars_data on car_names.makeid = cars_data.id where car_names.make = 'amc hornet sportabout (sw)'
select transcripts.transcript_date, transcripts.transcript_id from transcripts join transcript_contents on transcripts.transcript_id = transcript_contents.transcript_id group by transcripts.transcript_date order by count ( * ) asc limit 1
select name from employee where employee_id not in ( select employee_id from evaluation )
select count ( * ) from continents
select count ( * ), stuid from has_pet group by stuid
select manager_name, district from shop order by number_products desc limit 1
select language from countrylanguage order by percentage desc limit 1
select highschooler.name, count ( * ) from highschooler join friend on highschooler.id = friend.student_id group by friend.student_id
select count ( * ) from country where governmentform = 'Republic'
select airlines.airline from airlines join flights on airlines.airline = flights.airline where flights.destairport = 'AHD'
select ship.id, ship.name from ship join death on ship.id = death.caused_by_ship_id group by death.caused_by_ship_id order by sum ( death.injured ) desc limit 1
select owners.owner_id, owners.last_name from owners join dogs on owners.owner_id = dogs.owner_id join treatments on dogs.dog_id = treatments.dog_id group by owners.owner_id order by sum ( treatments.cost_of_treatment ) desc limit 1
select owners.first_name, dogs.name from owners join dogs on owners.owner_id = dogs.owner_id
select country.continent from country join countrylanguage on country.code = countrylanguage.countrycode group by country.continent order by count ( * ) desc limit 1
select count ( * ) from votes where state = 'NY' or state = 'CA'
select country_code, count ( * ) from players group by country_code
select count ( * ) from highschooler join friend on highschooler.id = friend.friend_id where highschooler.name = 'Kyle'
select conductor.name from orchestra join conductor on orchestra.conductor_id = conductor.conductor_id where orchestra.year_of_founded > 2008
select count ( * ) from cars_data where accelerate > ( select max ( accelerate ) from cars_data )
select created from votes where state = 'CA'
select max ( mpg ) from cars_data where cylinders = 8 or year < 1980
select id from tv_channel group by country having count ( * ) > 2
select tv_channel.series_name, tv_channel.country from tv_series join cartoon on tv_series.channel = cartoon.channel where cartoon.directed_by = 'Ben Jones' intersect select tv_channel.series_name, tv_channel.country from tv_series join cartoon on tv_series.channel = cartoon.id where cartoon.directed_by = 'Michael Chang'
select name from city where population between 160000 and 900000
select weight from pets where pet_age = ( select min ( pet_age ) from pets where pettype = 'dog' )
select package_option, series_name from tv_channel where hight_definition_tv = 'Yes'
select name from conductor order by year_of_work desc limit 1
select employee.name from employee join evaluation on employee.employee_id = evaluation.employee_id group by employee.employee_id order by count ( * ) desc limit 1
select song_name, song_release_year from singer order by age asc limit 1
select professional_id, role_code, email_address from professionals except select professionals.professional_id, professionals.role_code, professionals.email_address from professionals join treatments on professionals.professional_id = treatments.professional_id
select singer.name, count ( * ) from singer_in_concert join singer on singer_in_concert.singer_id = singer.singer_id group by singer.name
select template_id from templates except select template_id from documents
select country.name from country join countrylanguage on country.code = countrylanguage.countrycode group by country.name order by count ( * ) desc limit 1
select distinct breed_code, size_code from dogs
select count ( * ) from templates where template_type_code = 'CV'
select count ( distinct nationality ) from conductor
select owners.last_name from owners join dogs on owners.owner_id = dogs.owner_id order by dogs.age asc limit 1
select airportname from airports where airportcode = 'AKO'
select count ( * ) from dogs where age < ( select avg ( age ) from dogs )
select name from conductor order by year_of_work desc
select episode from tv_series order by rating asc
select people.birth_date from poker_player join people on poker_player.people_id = people.people_id order by poker_player.earnings asc limit 1
select count ( * ) from documents join templates on documents.template_id = templates.template_id where templates.template_type_code = 'PPT'
select name, tonnage from ship order by name desc
select car_makers.id, car_makers.maker from car_makers join model_list on car_makers.id = model_list.maker group by car_makers.id having count ( * ) >= 2 intersect select car_makers.id, car_makers.maker from car_makers join model_list on car_makers.id = model_list.maker group by car_makers.id having count ( * ) >= 2
select name from highschooler where id not in ( select student_id from friend )
select student_id, count ( * ) from friend group by student_id
select stuid from student except select stuid from has_pet join pets on has_pet.petid = pets.petid where pets.pettype = 'cat'
select count ( distinct location ) from shop
select count ( distinct template_id ) from documents
select document_name, template_id from documents where document_description like '%w%'
select owners.owner_id, owners.first_name, owners.last_name from owners join dogs on owners.owner_id = dogs.owner_id group by owners.owner_id order by count ( * ) desc limit 1
select professionals.professional_id, professionals.last_name, professionals.cell_number from treatments join professionals on treatments.professional_id = professionals.professional_id where professionals.state = 'Indiana' group by professionals.professional_id having count ( * ) > 2
select sum ( population ), max ( gnp ) from country where continent = 'Asia'
select semester_id from student_enrolment where degree_program_id = 'MA' intersect select semester_id from student_enrolment where degree_program_id = 'Bachelor'
select count ( * ), grade from highschooler group by grade
select students.first_name from addresses join students on addresses.address_id = students.permanent_address_id where addresses.country = 'Haiti' or students.cell_mobile_number = '09700166582'
select friend.name from highschooler join friend on highschooler.id = friend.friend_id where highschooler.name = 'Kyle'
select winner_name, loser_name from matches order by minutes desc limit 1
select count ( * ) from area_code_state
select count ( * ), shop.name from shop join hiring on shop.shop_id = hiring.shop_id group by hiring.shop_id
select name from people where nationality!= 'Russia'
select count ( * ) from employee
select winner_name, winner_rank from matches order by winner_age asc limit 3
select owners.first_name, dogs.name from owners join dogs on owners.owner_id = dogs.owner_id where owners.state = 'Virginia'
select count ( * ) from highschooler where grade = 9 or grade = 10
select car_makers.fullname, car_makers.id, count ( * ) from car_makers join model_list on car_makers.id = model_list.maker group by car_makers.id
select flightno from flights where sourceairport = 'APG'
select flights.flightno from airlines join flights on airlines.airline = flights.airline where airlines.airline = 'United Airlines'
select city.name from city join countrylanguage on city.countrycode = countrylanguage.countrycode where country.continent = 'Europe' and countrylanguage.language!= 'English'
select sum ( bonus ) from evaluation
select count ( * ) from singer
select birth_year, citizenship from singer
select district from shop where number_products < 3000 intersect select district from shop where number_products > 10000
select sum ( population ) from country where language!= 'English'
select count ( * ) from matches where year = 2013 or year = 2016
select id from tv_channel except select channel from cartoon where directed_by = 'Ben Jones'
select name, grade from highschooler
select contestants.contestant_number, contestants.contestant_name from contestants join votes on contestants.contestant_number = votes.contestant_number group by votes.contestant_number order by count ( * ) asc limit 1
select population, lifeexpectancy from country where name = 'Brazil'
select poker_player.money_rank from poker_player join people on poker_player.people_id = people.people_id order by people.height desc limit 1
select visit.total_spent from visitor join visit on visitor.id = visit.visitor_id where visitor.level_of_membership = 1
select count ( * ) from countrylanguage where language = 'Spanish' order by percentage desc limit 1
select document_id, document_name, document_description from documents
select country_code, count ( * ) from players group by country_code
select cars_data.cylinders from cars_data join model_list on cars_data.id = model_list.model where car_makers.maker = 'Volvo' order by cars_data.accelerate asc limit 1
select package_option from tv_channel where series_name = 'Sky Radio'
select email_address, cell_number, home_phone from professionals
select friend.name from highschooler join friend on highschooler.id = friend.student_id where highschooler.name = 'Kyle'
select template_id from documents group by template_id having count ( * ) > 1
select count ( * ) from countries join model_list on countries.countryid = model_list.country where countries.countryname = 'usa'
select sum ( surfacearea ) from country where region = 'Carribean'
select people.name from poker_player join people on poker_player.people_id = people.people_id order by poker_player.earnings desc
select count ( * ) from airports join flights on airports.airportcode = flights.sourceairport where airports.city = 'Aberdeen'
select citizenship, count ( * ) from singer group by citizenship
select players.first_name, players.country_code from players join matches on players.player_id = matches.winner_id where matches.tourney_name = 'WTA Championships' intersect select players.first_name, players.country_code from players join matches on players.player_id = matches.winner_id where matches.tourney_name = 'Australian Open'
select airlines.airline from airlines join flights on airlines.airline = flights.airline group by airlines.airline order by count ( * ) desc limit 1
select final_table_made, best_finish from poker_player
select count ( * ) from airports join flights on airports.airportcode = flights.sourceairport where airports.city = 'Aberdeen' or airports.city = 'Abilene'
select country from tv_channel except select tv_channel.country from tv_channel join cartoon on tv_channel.id = cartoon.channel where cartoon.written_by = 'Todd Casey'
select max ( cars_data.horsepower ), car_names.make from cars_data join car_names on cars_data.id = car_names.makeid where cars_data.cylinders = 3
select count ( * ) from players join matches on players.player_id = matches.winner_id where players.hand = 'left' and matches.tourney_name = 'WTA Championships'
select people.name from poker_player join people on poker_player.people_id = people.people_id
select department_description from departments where department_name like '%computer%'
select distinct car_names.model from cars_data join model_list on cars_data.id = model_list.model where cars_data.year > 1980
select paragraphs.paragraph_text from paragraphs join documents on paragraphs.document_id = documents.document_id where documents.document_name = 'Customer reviews'
select area_code_state.area_code from area_code_state join votes on area_code_state.state = votes.state group by area_code_state.area_code order by count ( * ) desc limit 1
select owners.first_name, dogs.name from owners join dogs on owners.owner_id = dogs.owner_id where owners.state = 'Virginia'
select petid, weight from pets where pet_age > 1
select degree_programs.degree_program_id, degree_programs.degree_summary_description from degree_programs join student_enrolment on degree_programs.degree_program_id = student_enrolment.degree_program_id group by student_enrolment.degree_program_id order by count ( * ) desc limit 1
select location, count ( * ) from shop group by location
select cell_mobile_number from students where first_name = 'Timmothy' and last_name = 'Ward'
select count ( distinct series_name ), count ( distinct content ) from tv_channel
select professionals.professional_id, professionals.role_code, professionals.first_name from treatments join professionals on treatments.professional_id = professionals.professional_id group by professionals.professional_id having count ( * ) >= 2
select count ( * ) from car_names join model_list on car_names.model = model_list.model join car_makers on model_list.maker = car_makers.id join countries on car_makers.country = countries.countryid where countries.countryname = 'United States'
select degree_program_id from student_enrolment group by degree_program_id order by count ( * ) desc limit 1
select ranking_date, sum ( tours ) from rankings group by ranking_date
select templates.template_type_code from templates join documents on templates.template_id = documents.template_id group by templates.template_type_code order by count ( * ) desc limit 1
select cylinders from cars_data where model = 'volvo' order by accelerate asc limit 1
select title from cartoon where directed_by = 'Ben Jones' or directed_by = 'Brandon Vietti'
select flights.flightno from airports join flights on airports.airportcode = flights.sourceairport where airports.city = 'Aberdeen'
select transcripts.transcript_date, transcripts.transcript_id from student_enrolment_courses join transcript_contents on student_enrolment_courses.student_course_id = transcript_contents.student_course_id group by transcripts.transcript_id having count ( * ) >= 2
select min ( share ), max ( share ) from tv_series
select semester_name from semesters where semester_id not in ( select semester_id from student_enrolment )
select first_name, middle_name, last_name from students order by date_first_registered asc limit 1
select model_list.model from cars_data join model_list on cars_data.id = model_list.model where cars_data.weight < ( select avg ( weight ) from cars_data )
select count ( * ) from templates
select countrycode from country where language!= 'English'
select sum ( population ) from city where district = 'Gelderland'
select tv_series.episode from tv_series join tv_channel on tv_series.channel = tv_channel.id where tv_channel.series_name = 'Sky Radio'
